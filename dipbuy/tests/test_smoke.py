"""Smoke tests for the paths test_replay.py does not cover: dry-run mode, live-account refusal,
automatic drawdown pause, daily cap, and the `check` command. Uses the fake MT5 and real data.

    python tests/test_smoke.py
"""
from __future__ import annotations

import contextlib
import io
import logging
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import engine                              # noqa: E402
from fake_mt5 import FakeMT5               # noqa: E402
from test_replay import bars_from_csv     # noqa: E402

TMP = Path(tempfile.mkdtemp())
FAILS: list[str] = []


def cfg(**acct):
    a = {"dry_run": True, "risk_pct": 0.5, "magic": 1, "max_open_positions": 99, "max_new_per_day": 99,
         "pause_drawdown_pct": 1e9, "pause_rolling_trades": 10**9, "max_lots": 1e9}
    a.update(acct)
    return {"account": a, "execution": {"max_spread_atr_frac": 1.0, "entry_valid_hours": 1e9, "deviation_points": 50},
            "telegram": {"bot_token": "", "chat_id": ""}, "symbols": {}}


def fresh(tag: str):
    engine.STATE_FILE, engine.JOURNAL = TMP / f"state_{tag}.json", TMP / f"journal_{tag}.csv"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name} {detail}")
    if not ok:
        FAILS.append(name)


def replay(fake, sym, c, k0=1, k1=None):
    for k in range(k0, k1 or len(fake.data[sym])):
        fake.set_index(sym, k)
        engine.run(fake, c)
        fake.play_bar(sym)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    us = bars_from_csv("usa500idxusd")

    # 1. dry run: no orders reach the broker, journal entries match live-mode entry days
    fresh("live"); live = FakeMT5({"US500": us}); replay(live, "US500", cfg(dry_run=False))
    fresh("dry"); dry = FakeMT5({"US500": us}); replay(dry, "US500", cfg(dry_run=True))
    import pandas as pd
    j = pd.read_csv(engine.JOURNAL)
    n_dry = int((j.event == "ENTRY_DRY").sum())
    check("dry run sends no orders", len(dry.sent) == 0, f"(orders sent: {len(dry.sent)})")
    check("dry run takes the same trades as live mode", n_dry == len(live.entries),
          f"(dry {n_dry} vs live {len(live.entries)})")
    n_stop_dry = int((j.event == "CLOSED_DRY").sum())
    jl = pd.read_csv(TMP / "journal_live.csv")
    n_stop_live = int(((jl.event == "CLOSED") & jl.detail.astype(str).str.startswith("stop loss")).sum())
    check("dry run detects the same stop-outs", n_stop_dry == n_stop_live, f"(dry {n_stop_dry} vs live {n_stop_live})")

    # 2. live account refused unless allow_live
    fresh("real"); real = FakeMT5({"US500": us}, demo=False)
    replay(real, "US500", cfg(dry_run=False), 1500, 1700)
    check("live account refused", len(real.sent) == 0, f"(orders sent: {len(real.sent)})")

    # 3. drawdown pause: equity drops 20% -> paused, no new entries afterwards
    fresh("dd"); f = FakeMT5({"US500": us})
    c = cfg(dry_run=False, pause_drawdown_pct=14)
    replay(f, "US500", c, 1, 1000)
    before = len(f.entries)
    f.balance *= 0.80
    replay(f, "US500", c, 1000, 1400)
    s = engine.load_state()
    check("drawdown pause triggers and blocks entries", s["paused"] and len(f.entries) == before,
          f"(paused={s['paused']} reason='{s['pause_reason']}' new entries after: {len(f.entries) - before})")

    # 4. daily cap: two instruments signalling together with max_new_per_day=1 -> one entry, one SKIP
    fresh("cap")
    spx = {b["time"]: b for b in bars_from_csv("usa500idxusd")}
    nas = {b["time"]: b for b in bars_from_csv("usatechidxusd")}
    times = sorted(spx.keys() & nas.keys())          # same calendar for both, so the fake clocks stay aligned
    a2, b2 = [spx[t] for t in times], [nas[t] for t in times]
    f = FakeMT5({"US500": a2, "NAS100": b2})
    c = cfg(dry_run=False, max_new_per_day=1)
    for k in range(1, len(a2)):
        for s_ in ("US500", "NAS100"):
            f.set_index(s_, k)
        engine.run(f, c)
        for s_ in ("US500", "NAS100"):
            f.play_bar(s_)
    jc = pd.read_csv(engine.JOURNAL)
    days = pd.Series([pd.Timestamp(t, unit="s").date() for t in f.entries])
    check("daily cap of 1 respected", days.value_counts().max() == 1 and (jc.event == "SKIP").sum() > 0,
          f"(max entries on one day {days.value_counts().max()}, skips {(jc.event == 'SKIP').sum()})")

    # 4b. dry -> live switch: open paper positions are dropped, real trading starts cleanly
    fresh("switch"); f = FakeMT5({"US500": us})
    k_sig = next(k for k in range(1500, len(us)) if us[k]["time"] in set(live.entries))   # a day the engine buys
    replay(f, "US500", cfg(dry_run=True), 1, k_sig + 1)
    had_dry = any(r.get("dry") for r in engine.load_state()["open"].values())
    replay(f, "US500", cfg(dry_run=False), k_sig + 1, k_sig + 2)
    left = [k for k, r in engine.load_state()["open"].items() if r.get("dry")]
    check("dry positions dropped when switching to live", had_dry and not left, f"(had dry {had_dry}, left {left})")

    # 4c. margin guard: exchange-style 100% margin, risk sized so one position needs more than the equity
    fresh("margin"); f = FakeMT5({"US500": us}, margin_rate=1.0)
    replay(f, "US500", cfg(dry_run=False, risk_pct=5.0), 1, 1200)
    jm = pd.read_csv(engine.JOURNAL)
    n_margin = int(jm.detail.astype(str).str.contains("free margin").sum()) if len(jm) else 0
    check("margin guard skips orders that do not fit", len(f.sent) == 0 and n_margin > 0,
          f"(orders {len(f.sent)}, margin skips {n_margin})")

    # 4d. index listed but trading disabled -> its ETF twin is traded instead, and check() says so
    spy = bars_from_csv("spyususd")
    f = FakeMT5({"US500": us, "SPY": spy}); f.disabled = {"US500"}
    dis = {}
    found = engine.resolve_symbols(f, cfg(), dis)
    check("disabled index -> ETF twin used", found == {"SPY": "SPY"} and dis == {"US500": "US500"}, f"({found}, {dis})")

    # 4e. error alerts: the same error is sent once, not every run; a recovery message clears it
    sent = []
    engine.Telegram.send = lambda self, text: sent.append(text) or True
    engine.ERR_FILE = TMP / "err.json"
    for _ in range(6):                                   # an hour of 10-minute runs with MT5 down
        engine.report_error(cfg(), "MT5 initialize failed: (-6, 'Terminal: Authorization failed')")
    n_err = len(sent)
    engine.report_error(cfg(), "a different error")      # a new error is always sent
    engine.report_recovered(cfg())
    engine.report_recovered(cfg())                       # only once
    check("error alerts throttled, recovery sent once", n_err == 1 and len(sent) == 3 and "recovered" in sent[-1]
          and not engine.ERR_FILE.exists(), f"(first hour {n_err} alert, total {len(sent)})")

    # 5. check command prints the table
    f = FakeMT5({"US500": us}); f.set_index("US500", len(us) - 1)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine.check(f, cfg())
    out = buf.getvalue()
    check("check command lists the instrument", "US500" in out and "SMA200" in out)
    print(out)

    print("SMOKE", "PASS" if not FAILS else f"FAIL {FAILS}")
    sys.exit(1 if FAILS else 0)
