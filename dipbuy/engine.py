"""Daily RSI(2) dip-buy engine for MetaTrader 5 (Windows).

Run it every 30-60 minutes (Windows Task Scheduler). Each run is idempotent:
  1. For every enabled instrument, read COMPLETED daily bars from your MT5 terminal.
  2. When a new daily bar has completed: queue an EXIT for open positions whose exit rule fired
     (RSI(2) > 70 or 10 days held) and queue an ENTRY where the entry rule fired.
  3. Execute queued orders once the market is open and the spread is sane:
     market BUY with a server-side stop at 3 x ATR, sized so the stop loses RISK_PCT of equity.
  4. Detect positions closed by their stop, record P/L in R, send Telegram messages,
     and pause automatically on drawdown or a negative 50-trade run.

Commands:
  python engine.py check           connect, show account + symbol mapping + today's readings (no orders)
  python engine.py run             the scheduled job (respects dry_run in config.toml)
  python engine.py status          open positions, queue, pause state, results so far
  python engine.py resume          clear an automatic pause after you have reviewed it
  python engine.py test-telegram   send a test message

Strategy rules live in strategy.py (validated in research/REPORT.md, sections 9-10).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import statistics
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import strategy as st

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
JOURNAL = HERE / "journal.csv"
LOCK = HERE / ".engine.lock"
LOG = HERE / "engine.log"
ERR_FILE = HERE / ".last_error.json"     # throttles repeated error alerts
ERR_REPEAT_S = 3600                      # same error is re-sent at most once an hour

# canonical name, broker-name candidates, twin index (ETF is skipped when its twin index is tradable)
UNIVERSE: list[tuple[str, list[str], str | None]] = [
    ("US500", ["US500", "SPX500", "USA500", "SP500", "US500CASH", "SPX", "S&P500", "US500.CASH"], None),
    ("NAS100", ["NAS100", "USTEC", "US100", "USTECH", "NDX100", "NASDAQ100", "NQ100", "US100.CASH", "USTEC.CASH"], None),
    ("US30", ["US30", "DJ30", "WS30", "USA30", "DOW30", "DJI30", "US30CASH", "US30.CASH"], None),
    ("JP225", ["JP225", "JPN225", "JAP225", "NIKKEI225", "NI225", "N225", "JAPAN225", "JP225CASH"], None),
    ("DE40", ["DE40", "GER40", "DAX40", "GER30", "DE30", "DAX", "DE40CASH", "GER40.CASH"], None),
    ("SPY", ["SPY"], "US500"), ("QQQ", ["QQQ"], "NAS100"), ("DIA", ["DIA"], "US30"),
    ("IWM", ["IWM"], None), ("DVY", ["DVY"], None), ("EFA", ["EFA"], None), ("XLF", ["XLF"], None),
    ("XLI", ["XLI"], None), ("XLK", ["XLK"], None), ("XLE", ["XLE"], None), ("XLV", ["XLV"], None),
    ("IYR", ["IYR"], None), ("SMH", ["SMH"], None), ("ARKQ", ["ARKQ"], None), ("JETS", ["JETS"], None),
]
# Deliberately NOT traded: UK100, HK50, CHINA50, EEM, single stocks, FX, metals, crypto
# (negative or unproven in validation - see research/REPORT.md).

OK_RETCODES = {10009, 10010}             # DONE, DONE_PARTIAL
RETRY_RETCODES = {10004, 10018, 10020, 10021, 10024, 10027, 10031}   # requote, market closed, price changed/off,
#                                                                 too many requests, Algo Trading off, no connection
ALGO_OFF = 10027

log = logging.getLogger("dipbuy")


# ---------------------------------------------------------------------------
# config / state / journal
# ---------------------------------------------------------------------------
def load_config() -> dict:
    p = HERE / "config.toml"
    if not p.exists():
        sys.exit("config.toml not found - copy config.example.toml to config.toml and edit it.")
    cfg = tomllib.loads(p.read_text(encoding="utf-8"))
    tg = cfg.setdefault("telegram", {})
    if not tg.get("bot_token") or not tg.get("chat_id"):
        env = read_env(HERE.parent / "bridge" / ".env")
        tg.setdefault("bot_token", "")
        tg["bot_token"] = tg["bot_token"] or env.get("TELEGRAM_BOT_TOKEN", "")
        tg["chat_id"] = str(tg.get("chat_id") or env.get("TELEGRAM_CHAT_ID", ""))
    return cfg


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_bar": {}, "queue": [], "open": {}, "equity_peak": 0.0, "paused": False,
            "pause_reason": "", "r_log": [], "new_today": {"day": "", "count": 0}, "no_entry_bar": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def journal(event: str, **kw) -> None:
    new = not JOURNAL.exists()
    fields = ["utc_time", "event", "symbol", "ticket", "price", "volume", "sl", "rsi", "atr", "risk_money",
              "pnl", "r", "detail"]
    with JOURNAL.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({"utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "event": event,
                    **{k: kw.get(k, "") for k in fields[2:]}})


# ---------------------------------------------------------------------------
# telegram (stdlib only)
# ---------------------------------------------------------------------------
class Telegram:
    def __init__(self, cfg: dict, dry: bool):
        self.token, self.chat = cfg["telegram"].get("bot_token", ""), str(cfg["telegram"].get("chat_id", ""))
        self.prefix = "🧪 DRY RUN · " if dry else ""

    def send(self, text: str) -> bool:
        if not self.token or not self.chat:
            log.warning("telegram not configured; message: %s", text)
            return False
        data = urllib.parse.urlencode({"chat_id": self.chat, "text": self.prefix + text,
                                       "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, data=data, timeout=10) as r:
                    if json.loads(r.read()).get("ok"):
                        return True
            except Exception as e:  # noqa: BLE001
                log.warning("telegram attempt %d failed: %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
        return False


# ---------------------------------------------------------------------------
# MT5 helpers
# ---------------------------------------------------------------------------
def connect(mt5, cfg: dict) -> None:
    m = cfg.get("mt5", {})
    kw = {}
    if m.get("terminal_path"):
        kw["path"] = m["terminal_path"]
    if m.get("login"):
        kw.update(login=int(m["login"]), password=m.get("password", ""), server=m.get("server", ""))
    if not mt5.initialize(**kw):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()} - is the terminal running and logged in?")


def is_demo(mt5, acc) -> bool:
    return acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO


def resolve_symbols(mt5, cfg: dict, disabled: dict | None = None) -> dict[str, str]:
    """canonical -> broker symbol for every instrument that is available and enabled.
    `disabled` (optional) collects canonical -> broker symbol for instruments listed but not tradable."""
    overrides = cfg.get("symbols", {})
    names = {s.name.upper(): s.name for s in (mt5.symbols_get() or [])}
    found: dict[str, str] = {}
    for canon, cands, _ in UNIVERSE:
        ov = str(overrides.get(canon, "")).strip()
        if ov == "-":
            continue
        pick = None
        if ov:
            pick = ov if mt5.symbol_info(ov) is not None else None
        else:
            for cand in cands:
                c = cand.upper()
                if c in names:
                    pick = names[c]; break
                # common broker decorations: SPY.US, #SPY, SPY.NYSE, US500.cash, US500m ...
                for up, real in names.items():
                    core = up.lstrip("#").split(".")[0].rstrip("M").rstrip("_")
                    if core == c:
                        pick = real; break
                if pick:
                    break
        if not pick:
            continue
        info = mt5.symbol_info(pick)
        if info is None or info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
            if disabled is not None and info is not None:
                disabled[canon] = pick
            continue
        mt5.symbol_select(pick, True)
        found[canon] = pick
    # skip an ETF when its twin index is tradable (same exposure twice)
    for canon, _, twin in UNIVERSE:
        if twin and twin in found and canon in found:
            del found[canon]
    return found


def daily_bars(mt5, sym: str, n: int = 450) -> list[dict]:
    """COMPLETED daily bars (start_pos=1 skips the forming bar) with session stubs removed:
    weekend-dated bars (Sunday-evening open on GMT+0 servers) and near-empty bars (< 10% of the
    median tick volume of the 20 bars before). Real but quiet days (half-day holidays) are kept,
    as in the backtest data."""
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, n)
    if rates is None or len(rates) == 0:
        return []
    bars = [{"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "tv": float(r["tick_volume"])} for r in rates]
    out = []
    for i, b in enumerate(bars):
        if datetime.fromtimestamp(b["time"], timezone.utc).weekday() >= 5:
            continue
        prev = [x["tv"] for x in bars[max(0, i - 20):i]]
        if len(prev) >= 10 and b["tv"] < 0.10 * statistics.median(prev):
            continue
        out.append(b)
    return out


def filling_mode(mt5, info) -> int:
    fm = info.filling_mode
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def volume_for_risk(mt5, sym: str, price: float, sl: float, risk_money: float, max_lots: float) -> float:
    info = mt5.symbol_info(sym)
    loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, 1.0, price, sl)
    if loss is None or loss >= 0:
        return 0.0
    vol = risk_money / abs(loss)
    step = info.volume_step or 0.01
    vol = int(vol / step + 1e-9) * step
    vol = min(vol, info.volume_max, max_lots)
    if vol < info.volume_min:
        return 0.0
    decimals = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1]))
    return round(vol, decimals)


def my_positions(mt5, magic: int) -> dict[int, object]:
    return {p.ticket: p for p in (mt5.positions_get() or []) if p.magic == magic}


# ---------------------------------------------------------------------------
# the scheduled job
# ---------------------------------------------------------------------------
def esc(x) -> str:
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar_start(server_ts: int) -> int:
    """Start of the daily bar containing a server timestamp (MT5 bar times are server-time midnights)."""
    return int(server_ts) - int(server_ts) % 86400


def run(mt5, cfg: dict) -> None:
    a, ex = cfg["account"], cfg.get("execution", {})
    dry = bool(a.get("dry_run", True))
    tg = Telegram(cfg, dry)
    magic = int(a.get("magic", 777201))
    state = load_state()

    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"no account info: {mt5.last_error()}")
    if not is_demo(mt5, acc) and not a.get("allow_live", False) and not dry:
        tg.send("⛔ Engine refused to trade: this is a LIVE account and allow_live = false in config.toml.")
        log.error("refusing to trade a live account")
        return

    symbols = resolve_symbols(mt5, cfg)
    state["symbols"] = symbols
    positions = my_positions(mt5, magic)
    cur = esc(acc.currency)
    no_entry = state.setdefault("no_entry_bar", {})   # canon -> bar of a stop-out (backtest: no re-entry on that bar)
    if not dry:                                       # switched dry -> live: paper positions must not block real ones
        for key in [k for k, r in state["open"].items() if r.get("dry")]:
            rec = state["open"].pop(key)
            journal("DRY_DROPPED", symbol=rec["symbol"], ticket=key, detail="dry-run position dropped: now trading")

    # ---- 1. real positions that disappeared since last run (stop hit, our exit, or manual) -> record R
    for key in list(state["open"]):
        rec = state["open"][key]
        if rec.get("dry") or int(key) in positions:
            continue
        state["open"].pop(key)
        deals = sorted(mt5.history_deals_get(position=int(rec.get("position_id", key))) or [], key=lambda d: d.time)
        pnl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
        exit_px = deals[-1].price if deals else 0.0
        r = pnl / rec["risk_money"] if rec.get("risk_money") else 0.0
        state["r_log"].append(round(r, 4))
        why = rec.get("exit_reason") or "stop loss / closed manually"
        if not rec.get("exit_reason") and deals:
            no_entry[rec["canon"]] = bar_start(deals[-1].time)
        journal("CLOSED", symbol=rec["symbol"], ticket=key, price=exit_px, pnl=round(pnl, 2), r=round(r, 3), detail=why)
        tg.send(f"{'✅' if pnl > 0 else '❌'} <b>CLOSED {esc(rec['canon'])}</b> ({esc(rec['symbol'])})\n"
                f"Exit {exit_px} · {esc(why)}\nP/L {pnl:+.2f} {cur} = <b>{r:+.2f}R</b>")

    # ---- 2. risk state: equity-peak drawdown and rolling expectancy -> automatic pause
    state["equity_peak"] = max(state.get("equity_peak", 0.0), acc.equity)
    dd = 100 * (1 - acc.equity / state["equity_peak"]) if state["equity_peak"] > 0 else 0.0
    n_roll = int(a.get("pause_rolling_trades", 50))
    if not state["paused"]:
        if dd >= float(a.get("pause_drawdown_pct", 14.0)):
            state["paused"], state["pause_reason"] = True, f"drawdown {dd:.1f}% from equity peak"
        elif len(state["r_log"]) >= n_roll and sum(state["r_log"][-n_roll:]) < 0:
            state["paused"], state["pause_reason"] = True, f"last {n_roll} trades net negative"
        if state["paused"]:
            tg.send(f"⏸ <b>Engine paused</b>: {esc(state['pause_reason'])}. Open positions keep their stops; "
                    f"no new entries until you run <code>python engine.py resume</code>.")
            journal("PAUSE", detail=state["pause_reason"])

    # ---- 3. new completed daily bars -> decisions
    queued = {(q["kind"], q["canon"]) for q in state["queue"]}
    candidates, new_bars = [], 0
    for canon, sym in symbols.items():
        bars = daily_bars(mt5, sym)
        snap = st.snapshot(bars)
        if snap is None or state["last_bar"].get(canon) == snap.bar_time:
            continue                                      # no data yet / already decided on this bar
        state["last_bar"][canon] = snap.bar_time
        new_bars += 1
        for key, rec in list(state["open"].items()):
            if rec["canon"] != canon:
                continue
            since = [b for b in bars if b["time"] >= rec["entry_bar_time"]]
            hit = next((b for b in since if b["low"] <= rec["sl"]), None) if rec.get("dry") else None
            if hit:
                # dry run: no broker stop exists, so detect a stop hit from the bars
                state["open"].pop(key)
                no_entry[canon] = hit["time"]
                journal("CLOSED_DRY", symbol=sym, ticket=key, price=rec["sl"], r=-1.0, detail="stop would have been hit")
                tg.send(f"❌ <b>{esc(canon)}</b> stop {rec['sl']} would have been hit (≈ −1R)")
                continue
            reason = st.exit_due(snap, len(since))
            if reason and ("exit", canon) not in queued:
                state["queue"].append({"kind": "exit", "canon": canon, "symbol": sym, "key": key,
                                       "reason": reason, "queued": int(time.time())})
                queued.add(("exit", canon))
                log.info("exit queued %s: %s", canon, reason)
        held = {rec["canon"] for rec in state["open"].values()}
        if (snap.entry_signal and canon not in held and ("entry", canon) not in queued and not state["paused"]
                and snap.bar_time > no_entry.get(canon, -1)):
            candidates.append((snap.rsi, canon, sym, snap))
    if candidates:                                        # daily cap counts per signal day, not wall-clock day
        day = datetime.fromtimestamp(max(c[3].bar_time for c in candidates), timezone.utc).strftime("%Y-%m-%d")
        if state["new_today"]["day"] != day:
            state["new_today"] = {"day": day, "count": 0}
    max_open = int(a.get("max_open_positions", 10))
    max_new = int(a.get("max_new_per_day", 5))
    slots = max(0, min(max_open - len(state["open"]) - sum(q["kind"] == "entry" for q in state["queue"]),
                       max_new - state["new_today"]["count"]))
    candidates.sort(key=lambda c: c[0])                   # crowded days: deepest dip first
    for rsi, canon, sym, snap in candidates[:slots]:
        state["queue"].append({"kind": "entry", "canon": canon, "symbol": sym, "rsi": snap.rsi, "atr": snap.atr,
                               "signal_bar_time": snap.bar_time, "close": snap.close, "sma200": snap.sma200,
                               "queued": int(time.time()),
                               "expires": int(time.time() + 3600 * float(ex.get("entry_valid_hours", 30)))})
        state["new_today"]["count"] += 1
        log.info("entry queued %s rsi=%.1f", canon, rsi)
    for rsi, canon, sym, snap in candidates[slots:]:
        journal("SKIP", symbol=sym, rsi=round(rsi, 1), detail="position/day limit reached")
        tg.send(f"⏭ Signal on {esc(canon)} skipped (RSI {rsi:.1f}): position or daily limit reached.")

    # ---- 4. execute the queue (exits first)
    risk_pct = float(a.get("risk_pct", 0.5))
    dev = int(ex.get("deviation_points", 50))
    max_spread_frac = float(ex.get("max_spread_atr_frac", 0.05))
    remaining = []
    for q in sorted(state["queue"], key=lambda q: q["kind"] != "exit"):
        sym = q["symbol"]
        info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        if info is None or tick is None or tick.bid <= 0:
            remaining.append(q); continue
        if q["kind"] == "exit":
            rec = state["open"].get(q["key"])
            if rec is None:
                continue                                   # already gone (stop hit)
            if rec.get("dry"):
                r = (tick.bid - rec["entry"]) / (rec["entry"] - rec["sl"]) if rec["entry"] > rec["sl"] else 0.0
                state["open"].pop(q["key"])
                journal("EXIT_DRY", symbol=sym, ticket=q["key"], price=tick.bid, r=round(r, 3), detail=q["reason"])
                tg.send(f"🔵 <b>EXIT {esc(q['canon'])}</b> ({esc(sym)}) at ~{tick.bid} · {esc(q['reason'])} · ≈ {r:+.2f}R")
                continue
            pos = positions.get(int(q["key"]))
            if pos is None:
                continue
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": pos.volume, "type": mt5.ORDER_TYPE_SELL,
                   "position": pos.ticket, "price": tick.bid, "deviation": dev, "magic": magic,
                   "comment": "DIPBUY exit", "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode(mt5, info)}
            res = mt5.order_send(req)
            if res is not None and res.retcode in OK_RETCODES:
                rec["exit_reason"] = q["reason"]           # P/L is recorded in step 1 of the next run
                journal("EXIT_SENT", symbol=sym, ticket=q["key"], price=res.price, detail=q["reason"])
            else:
                log.warning("exit %s not done (retcode %s %s)", sym, getattr(res, "retcode", None),
                            getattr(res, "comment", mt5.last_error()))
                remaining.append(q)
            continue
        # ---- entry
        if time.time() > q["expires"]:
            journal("EXPIRED", symbol=sym, rsi=round(q["rsi"], 1), detail="not filled in time (market closed / spread)")
            tg.send(f"⌛ Entry on {esc(q['canon'])} expired unfilled (market closed or spread too wide).")
            continue
        spread = tick.ask - tick.bid
        if spread > max_spread_frac * q["atr"]:
            log.info("entry %s waiting: spread %s > %.0f%% of ATR", sym, spread, 100 * max_spread_frac)
            remaining.append(q); continue
        price = tick.ask
        sl = round(st.stop_price(price, q["atr"]), info.digits)
        if sl >= tick.bid - info.trade_stops_level * info.point:
            journal("SKIP", symbol=sym, detail="stop inside broker stops level"); continue
        vol = volume_for_risk(mt5, sym, price, sl, acc.equity * risk_pct / 100.0, float(a.get("max_lots", 50)))
        if vol <= 0:
            journal("SKIP", symbol=sym, rsi=round(q["rsi"], 1), detail="volume below broker minimum for this risk")
            tg.send(f"⚠️ Signal on {esc(q['canon'])} skipped: the minimum lot size would risk more than {risk_pct}%.")
            continue
        loss_per_lot = abs(mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, 1.0, price, sl) or 0.0)
        risk_money = round(vol * loss_per_lot, 2)
        margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, vol, price) or 0.0
        free = (mt5.account_info() or acc).margin_free
        if dry:                                            # paper positions hold no broker margin: count them here
            free -= sum(r.get("margin", 0.0) for r in state["open"].values() if r.get("dry"))
        if margin > 0.95 * free:
            journal("SKIP", symbol=sym, rsi=round(q["rsi"], 1), detail=f"not enough free margin ({margin:.0f} needed, {free:.0f} free)")
            tg.send(f"⏭ Signal on {esc(q['canon'])} skipped: needs {margin:.0f} {cur} margin, only {free:.0f} free.")
            continue
        sp_txt = f"spread {spread:.{info.digits}f} ({100 * spread / q['atr']:.1f}% of ATR)"

        def msg(fill_px: float, verb: str) -> str:
            return (f"🟢 <b>BUY {esc(q['canon'])}</b> ({esc(sym)})\n{verb} {fill_px} · Stop {sl} (3×ATR)\n"
                    f"Exit: when RSI(2) closes above 70, or after 10 days\n"
                    f"RSI(2) {q['rsi']:.1f} · close {q['close']} &gt; SMA200 {q['sma200']:.2f}\n"
                    f"Size {vol} · risk ≈ {risk_money:.2f} {cur} ({risk_pct}%)")
        if dry:
            key = f"dry-{q['canon']}-{q['signal_bar_time']}"
            state["open"][key] = {"canon": q["canon"], "symbol": sym, "entry_bar_time": bar_start(tick.time),
                                  "risk_money": risk_money, "entry": price, "sl": sl, "dry": True, "margin": margin}
            journal("ENTRY_DRY", symbol=sym, ticket=key, price=price, volume=vol, sl=sl, rsi=round(q["rsi"], 1),
                    atr=round(q["atr"], 6), risk_money=risk_money, detail=sp_txt)
            tg.send(msg(price, "Entry ~"))
            continue
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": vol, "type": mt5.ORDER_TYPE_BUY,
               "price": price, "sl": sl, "tp": 0.0, "deviation": dev, "magic": magic, "comment": "DIPBUY",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling_mode(mt5, info)}
        res = mt5.order_send(req)
        if res is None or res.retcode not in OK_RETCODES:
            rc = getattr(res, "retcode", None)
            log.warning("entry %s not done (retcode %s %s)", sym, rc, getattr(res, "comment", mt5.last_error()))
            if rc == ALGO_OFF and not q.get("algo_warned"):
                q["algo_warned"] = True
                tg.send(f"⚠️ Order on {esc(q['canon'])} blocked: <b>Algo Trading is switched off in MT5</b>. "
                        f"Click the Algo Trading button in the MT5 toolbar; the engine retries on its next run.")
            if rc is None or rc in RETRY_RETCODES:
                remaining.append(q)
            else:
                journal("REJECTED", symbol=sym, detail=f"retcode {rc} {getattr(res, 'comment', '')}")
                tg.send(f"⚠️ Order on {esc(q['canon'])} rejected by the broker (code {rc}).")
            continue
        pos = next((p for p in my_positions(mt5, magic).values()
                    if p.symbol == sym and str(p.ticket) not in state["open"]), None)
        ticket = pos.ticket if pos else res.order
        fill = pos.price_open if pos else res.price
        state["open"][str(ticket)] = {"canon": q["canon"], "symbol": sym,
                                      "position_id": getattr(pos, "identifier", ticket) if pos else ticket,
                                      "entry_bar_time": bar_start(pos.time if pos else tick.time),
                                      "risk_money": risk_money, "entry": fill, "sl": sl}
        journal("ENTRY", symbol=sym, ticket=ticket, price=fill, volume=vol, sl=sl, rsi=round(q["rsi"], 1),
                atr=round(q["atr"], 6), risk_money=risk_money, detail=sp_txt)
        tg.send(msg(fill, "Filled"))
    state["queue"] = remaining
    save_state(state)
    log.info("run ok | %s | %d instruments | %d new daily bars | %d signals | %d queued | %d open%s",
             "DRY" if dry else "TRADING", len(symbols), new_bars, len(candidates), len(remaining), len(state["open"]),
             " | PAUSED: " + state["pause_reason"] if state["paused"] else "")


# ---------------------------------------------------------------------------
# other commands
# ---------------------------------------------------------------------------
def check(mt5, cfg: dict) -> None:
    acc = mt5.account_info()
    print(f"Account {acc.login} @ {acc.server} | {'DEMO' if is_demo(mt5, acc) else 'LIVE'} | "
          f"equity {acc.equity:.2f} {acc.currency} | dry_run={cfg['account'].get('dry_run', True)}")
    disabled: dict[str, str] = {}
    syms = resolve_symbols(mt5, cfg, disabled)
    print(f"\n{len(syms)} tradable instruments found:")
    print("Readings are from each instrument's last COMPLETED daily bar (a bar counts as complete once the next one starts).")
    print(f"{'canonical':10s} {'broker symbol':16s} {'bar date':10s} {'close':>12s} {'SMA200':>12s} {'RSI(2)':>7s} {'ATR':>10s}  signal")
    for canon, _, _ in UNIVERSE:
        if canon not in syms:
            continue
        snap = st.snapshot(daily_bars(mt5, syms[canon]))
        if snap is None:
            print(f"{canon:10s} {syms[canon]:16s}   (not enough daily history yet)")
            continue
        sig = "BUY" if snap.entry_signal else ("uptrend" if snap.uptrend else "below SMA200")
        day = datetime.fromtimestamp(snap.bar_time, timezone.utc).strftime("%Y-%m-%d")
        print(f"{canon:10s} {syms[canon]:16s} {day:10s} {snap.close:12.4f} {snap.sma200:12.4f} {snap.rsi:7.1f} {snap.atr:10.4f}  {sig}")
    if disabled:
        print("\nListed but TRADING DISABLED on this account (quotes only):",
              ", ".join(f"{c} ({b})" for c, b in disabled.items()))
    missing = [c for c, _, tw in UNIVERSE if c not in syms and c not in disabled and not (tw and tw in syms)]
    if missing:
        print("\nNot found at this broker (add the exact name under [symbols] in config.toml if it exists):", ", ".join(missing))


def status() -> None:
    s = load_state()
    print(f"paused: {s['paused']} {s.get('pause_reason', '')}")
    print("open:", json.dumps(s["open"], indent=1))
    print("queue:", json.dumps(s["queue"], indent=1))
    r = s["r_log"]
    if r:
        w = [x for x in r if x > 0]
        print(f"closed trades {len(r)} | win {100 * len(w) / len(r):.1f}% | avg {sum(r) / len(r):+.3f}R | total {sum(r):+.2f}R")


def report_error(cfg: dict, text: str) -> None:
    """Telegram the error, but the SAME error at most once an hour (runs every 10 min while MT5 is down)."""
    now = time.time()
    try:
        prev = json.loads(ERR_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prev = {}
    same = prev.get("msg") == text
    if not same or now - prev.get("sent", 0) >= ERR_REPEAT_S:
        mins = int((now - prev.get("first", now)) / 60) if same else 0
        Telegram(cfg, False).send(f"🛑 Engine error{f' (still failing after {mins} min)' if same else ''}: {esc(text)}\n"
                                  "Usually MT5 is closed, logged out or on another account. Repeats at most hourly.")
        prev = {"msg": text, "sent": now, "first": prev.get("first", now) if same else now}
    ERR_FILE.write_text(json.dumps(prev), encoding="utf-8")


def report_recovered(cfg: dict) -> None:
    if ERR_FILE.exists():
        Telegram(cfg, False).send("✅ Engine recovered: the MT5 connection works again.")
        ERR_FILE.unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "status":
        status(); return
    if cmd == "resume":
        s = load_state(); s["paused"], s["pause_reason"] = False, ""; save_state(s)
        print("pause cleared"); return
    cfg = load_config()
    if cmd == "test-telegram":
        print("sent" if Telegram(cfg, False).send("✅ Dip-buy engine: Telegram works.") else "FAILED"); return
    import MetaTrader5 as mt5   # imported late so status/resume work without MT5
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - LOCK.stat().st_mtime < 1200:
            print("another run is in progress"); return
        LOCK.unlink(); fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        connect(mt5, cfg)
        if cmd == "check":
            check(mt5, cfg)
        elif cmd == "run":
            run(mt5, cfg)
            report_recovered(cfg)
        else:
            print(__doc__)
    except Exception:
        log.exception("run failed")
        try:
            report_error(cfg, str(sys.exc_info()[1]))
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        mt5.shutdown()
        os.close(fd)
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
