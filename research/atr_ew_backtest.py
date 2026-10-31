"""Bar-by-bar Python port of pine/atr_ew_hybrid.pine for research on long histories.

Mirrors the Pine rules: ATR regime filter, ADX, UTC session, ATR zigzag pivots,
Setup A (W3) and Setup B (ABC), stop/target ladder, partial exits 40/30/30,
break-even after TP1, stop -> TP1 after TP2, cooldown, max signals/day,
one position at a time. Signals are evaluated on bar close; entries fill at the
NEXT bar's open (same as TradingView's strategy engine).

Cost model (bid data):
  * long  entry at ask = bid_open + spread (+ slip), exits on bid
  * short entry at bid_open (- slip), exits on ask = bid + spread
  * stop fills get `slip` extra; limit (TP) fills get none
  * if a bar touches both stop and a target, the STOP is assumed first (conservative)

Results are in R (multiples of the initial risk from the actual fill to the stop),
so position sizing does not change them.

Usage:
  python atr_ew_backtest.py data/xauusd_m15.csv [--spread 0.30] [--slip 0.05]
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field, asdict, replace

import numpy as np
import pandas as pd


@dataclass
class Params:
    atr_len: int = 14
    atr_base_len: int = 100
    min_atr_ratio: float = 0.8
    max_atr_ratio: float = 2.5
    use_adx: bool = True
    adx_len: int = 14
    min_adx: float = 18.0
    piv_l: int = 5
    piv_r: int = 3
    min_swing_atr: float = 1.0
    use_w3: bool = True
    w2_min: float = 0.382
    w2_max: float = 0.786
    min_w1_atr: float = 2.0
    w3_trend_req: bool = False
    use_abc: bool = True
    b_min: float = 0.236
    b_max: float = 0.886
    c_min: float = 0.618
    c_max: float = 1.618
    min_a_atr: float = 1.5
    trend_len: int = 200
    trig_frac: float = 1.0
    max_chase_atr: float = 0.5
    max_bars_piv: int = 20
    sl_mode: str = "tighter"          # structural | atr | tighter
    sl_buf_atr: float = 0.25
    sl_atr: float = 1.5
    min_sl_atr: float = 0.5
    max_sl_atr: float = 3.0
    tp_fib: tuple = (1.0, 1.618, 2.618)
    r_floor: tuple = (1.0, 2.0, 3.0)
    tp_pct: tuple = (0.40, 0.30)      # remainder runs to TP3
    be_after_tp1: bool = True
    trail_after_tp2: bool = True
    use_session: bool = True
    session_utc: tuple = (7 * 60, 20 * 60)   # minutes of day [start, end)
    cooldown: int = 8
    max_per_day: int = 3
    spread: float = 0.30              # price units (USD/oz for gold), includes commission
    slip: float = 0.05
    spread_bps: float = 0.0           # extra spread as basis points of price (crypto/indices)
    slip_bps: float = 0.0
    swap_bps: float = 0.0             # financing cost per overnight rollover (22:00 UTC), bps of price
    # --- research extensions (defaults reproduce the Pine script) ---
    tp_single_r: float | None = None  # if set: one full exit at entry +/- tp_single_r * risk
    time_stop: int = 0                # bars after entry; 0 = off. Exit at bar close.
    entry_delay: int = 0              # stress test: fill N bars later than normal


# ---------------------------------------------------------------------------
# Indicators (Pine-equivalent)
# ---------------------------------------------------------------------------
def rma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    if len(x) < n:
        return out
    # seed with SMA of the first n valid values (Pine ta.rma)
    start = np.argmax(~np.isnan(x))
    if start + n > len(x):
        return out
    out[start + n - 1] = np.nanmean(x[start:start + n])
    a = 1.0 / n
    for i in range(start + n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def indicators(df: pd.DataFrame, p: Params) -> dict:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    c1 = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - c1), np.abs(l - c1)]), axis=0)
    tr[0] = h[0] - l[0]
    atr = rma(tr, p.atr_len)
    atr_base = pd.Series(atr).rolling(p.atr_base_len).mean().to_numpy().copy()
    h1, l1 = np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]]
    up, dn = h - h1, l1 - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pdm[0] = mdm[0] = 0.0
    trur = rma(tr, p.adx_len)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus = 100 * rma(pdm, p.adx_len) / trur
        minus = 100 * rma(mdm, p.adx_len) / trur
        s = plus + minus
        dx = 100 * np.abs(plus - minus) / np.where(s == 0, 1, s)
    adx = rma(np.nan_to_num(dx, nan=0.0), p.adx_len)
    ema = pd.Series(c).ewm(span=p.trend_len, adjust=False).mean().to_numpy().copy()
    ema[: p.trend_len - 1] = np.nan
    # pivots (strict on both sides), confirmed at bar j + R
    L, R = p.piv_l, p.piv_r
    hs, ls = pd.Series(h), pd.Series(l)
    left_max = hs.shift(1).rolling(L).max().to_numpy()
    right_max = hs[::-1].shift(1).rolling(R).max()[::-1].to_numpy()
    left_min = ls.shift(1).rolling(L).min().to_numpy()
    right_min = ls[::-1].shift(1).rolling(R).min()[::-1].to_numpy()
    is_ph = (h > left_max) & (h > right_max)
    is_pl = (l < left_min) & (l < right_min)
    return dict(atr=atr, atr_ratio=atr / atr_base, adx=adx, ema=ema, is_ph=is_ph, is_pl=is_pl)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    signal_ts: int
    entry_ts: int
    direction: int
    setup: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    r: float = 0.0
    r_gross: float = 0.0
    exit_ts: int = 0
    outcome: str = ""
    atr: float = 0.0
    bars: int = 0
    nights: int = 0


def run(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    ind = indicators(df, p)
    ts = df.index.to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr, atr_ratio, adx, ema = ind["atr"], ind["atr_ratio"], ind["adx"], ind["ema"]
    is_ph, is_pl = ind["is_ph"], ind["is_pl"]
    minute = ((ts // 60_000) % 1440).astype(int)
    day = ts // 86_400_000
    n = len(df)
    R = p.piv_r

    zzP: list[float] = []
    zzB: list[int] = []
    zzD: list[int] = []

    def push(pr, b, d):
        zzP.append(pr); zzB.append(b); zzD.append(d)
        if len(zzP) > 12:
            del zzP[0], zzB[0], zzD[0]

    def add_pivot(pr, b, d, min_swing):
        if not zzP:
            push(pr, b, d); return
        if zzD[-1] == d:
            if (d == 1 and pr > zzP[-1]) or (d == -1 and pr < zzP[-1]):
                zzP[-1], zzB[-1] = pr, b
        else:
            right_side = pr > zzP[-1] if d == 1 else pr < zzP[-1]
            if right_side and abs(pr - zzP[-1]) >= min_swing:
                push(pr, b, d)

    trades: list[Trade] = []
    last_used_piv = -1
    last_signal_bar = -10 ** 9
    signals_today = 0
    cur_day = None
    pending: Trade | None = None          # ordered at bar close, fills next open
    pending_bar = 0
    pos: dict | None = None               # open position state

    for i in range(n):
        # ---- day reset (UTC)
        if day[i] != cur_day:
            cur_day, signals_today = day[i], 0

        sp = p.spread + o[i] * p.spread_bps * 1e-4
        sl_ = p.slip + o[i] * p.slip_bps * 1e-4

        # ---- fill pending entry at this bar's open
        if pending is not None and i >= pending_bar:
            t = pending
            pending = None
            d = t.direction
            fill = o[i] + sp + sl_ if d == 1 else o[i] - sl_
            fill_gross = o[i]
            risk = (fill - t.sl) * d
            if risk <= 0 or (t.tp1 - fill) * d <= 0:
                pass  # gapped through SL or TP1 before entry: skip (EA would skip too)
            else:
                t.entry_ts, t.entry = int(ts[i]), fill
                pos = dict(t=t, fill=fill, fill_gross=fill_gross, risk=risk,
                           risk_gross=abs(fill_gross - t.sl), stop=t.sl, stage=0, rem=1.0,
                           pnl=0.0, pnl_gross=0.0, next_stop=None, bars=0)

        # ---- manage open position on this bar
        if pos is not None:
            t, d = pos["t"], pos["t"].direction
            pos["bars"] += 1
            if pos["next_stop"] is not None:
                pos["stop"], pos["next_stop"] = pos["next_stop"], None
            # adverse/favourable extremes in the price we would exit at
            if d == 1:
                adv, fav, opn, cls = l[i], h[i], o[i], c[i]
            else:
                adv, fav, opn, cls = h[i] + sp, l[i] + sp, o[i] + sp, c[i] + sp
            stop = pos["stop"]
            stop_hit = (adv <= stop) if d == 1 else (adv >= stop)
            closed = False
            if stop_hit:
                px = (min(stop, opn) if d == 1 else max(stop, opn)) - d * sl_
                pos["pnl"] += pos["rem"] * (px - pos["fill"]) * d
                pos["pnl_gross"] += pos["rem"] * (stop - pos["fill_gross"]) * d  # gross: at the level, no costs
                closed, outcome = True, ("SL" if pos["stage"] == 0 else f"TP{pos['stage']}+stop")
            else:
                tps = (t.tp1, t.tp2, t.tp3)
                fracs = (1.0, 0.0, None) if p.tp_single_r else (p.tp_pct[0], p.tp_pct[1], None)
                while pos["stage"] < 3:
                    k = pos["stage"]
                    tp = tps[k]
                    if not ((fav >= tp) if d == 1 else (fav <= tp)):
                        break
                    q = pos["rem"] if fracs[k] is None else min(fracs[k], pos["rem"])
                    pos["pnl"] += q * (tp - pos["fill"]) * d
                    pos["pnl_gross"] += q * (tp - pos["fill_gross"]) * d
                    pos["rem"] -= q
                    pos["stage"] += 1
                    if pos["rem"] <= 1e-9:
                        closed, outcome = True, ("TP" if p.tp_single_r else "TP3")
                        break
                if not closed and p.time_stop and pos["bars"] >= p.time_stop:
                    px = cls - d * sl_
                    pos["pnl"] += pos["rem"] * (px - pos["fill"]) * d
                    pos["pnl_gross"] += pos["rem"] * (c[i] - pos["fill_gross"]) * d
                    closed, outcome = True, "TIME"
                if not closed:
                    if pos["stage"] >= 2 and p.trail_after_tp2:
                        pos["next_stop"] = t.tp1
                    elif pos["stage"] >= 1 and p.be_after_tp1:
                        pos["next_stop"] = pos["fill"]
            if closed:
                nights = int((ts[i] + 7_200_000) // 86_400_000 - (t.entry_ts + 7_200_000) // 86_400_000)
                swap = nights * pos["fill"] * p.swap_bps * 1e-4
                t.r = (pos["pnl"] - swap) / pos["risk"]
                t.r_gross = pos["pnl_gross"] / pos["risk_gross"]
                t.exit_ts, t.outcome, t.bars, t.nights = int(ts[i]), outcome, pos["bars"], nights
                trades.append(t)
                pos = None

        # ---- zigzag update with pivots confirmed at this bar
        if i >= R and not math.isnan(atr[i]):
            j = i - R
            a_piv = atr[j] if not math.isnan(atr[j]) else atr[i]
            min_swing = a_piv * p.min_swing_atr
            ph, pl = is_ph[j], is_pl[j]
            last_dir = zzD[-1] if zzD else 0
            if ph and pl:
                if last_dir == 1:
                    add_pivot(l[j], j, -1, min_swing); add_pivot(h[j], j, 1, min_swing)
                else:
                    add_pivot(h[j], j, 1, min_swing); add_pivot(l[j], j, -1, min_swing)
            elif ph:
                add_pivot(h[j], j, 1, min_swing)
            elif pl:
                add_pivot(l[j], j, -1, min_swing)

        a = atr[i]
        if math.isnan(a) or math.isnan(atr_ratio[i]):
            continue

        # ---- setup detection
        sig_dir, setup, stop_piv, tp_base, tp_len, piv_bar = 0, "", 0.0, 0.0, 0.0, -1
        npv = len(zzP)
        if p.use_w3 and npv >= 3:
            p0, p1, p2, b2, d2 = zzP[-3], zzP[-2], zzP[-1], zzB[-1], zzD[-1]
            w1, w2 = abs(p1 - p0), abs(p1 - p2)
            retr = w2 / w1 if w1 > 0 else math.nan
            bars_since = i - b2
            shape_ok = (not math.isnan(retr) and p.w2_min <= retr <= p.w2_max and w1 >= p.min_w1_atr * a
                        and bars_since <= p.max_bars_piv and b2 != last_used_piv)
            if shape_ok and d2 == -1 and p2 > p0 and (not p.w3_trend_req or c[i] > ema[i]):
                trig = p2 + p.trig_frac * (p1 - p2)
                if c[i] > trig and c[i] - trig <= p.max_chase_atr * a and l[i - bars_since + 1:i + 1].min() > p2:
                    sig_dir, setup, stop_piv, tp_base, tp_len, piv_bar = 1, "W3", p2, p2, w1, b2
            if sig_dir == 0 and shape_ok and d2 == 1 and p2 < p0 and (not p.w3_trend_req or c[i] < ema[i]):
                trig = p2 - p.trig_frac * (p2 - p1)
                if c[i] < trig and trig - c[i] <= p.max_chase_atr * a and h[i - bars_since + 1:i + 1].max() < p2:
                    sig_dir, setup, stop_piv, tp_base, tp_len, piv_bar = -1, "W3", p2, p2, w1, b2
        if sig_dir == 0 and p.use_abc and npv >= 4 and not math.isnan(ema[i]):
            x0, pa, pb, pc = zzP[-4], zzP[-3], zzP[-2], zzP[-1]
            bc, dc = zzB[-1], zzD[-1]
            len_a, len_c = abs(x0 - pa), abs(pb - pc)
            b_retr = abs(pb - pa) / len_a if len_a > 0 else math.nan
            c_rat = len_c / len_a if len_a > 0 else math.nan
            bars_since = i - bc
            shape_ok = (not math.isnan(b_retr) and p.b_min <= b_retr <= p.b_max and p.c_min <= c_rat <= p.c_max
                        and len_a >= p.min_a_atr * a and bars_since <= p.max_bars_piv and bc != last_used_piv)
            if shape_ok and dc == -1 and pb < x0 and pc < pa and c[i] > ema[i]:
                trig = pc + p.trig_frac * (pb - pc)
                if c[i] > trig and c[i] - trig <= p.max_chase_atr * a and l[i - bars_since + 1:i + 1].min() > pc:
                    sig_dir, setup, stop_piv, tp_base, tp_len, piv_bar = 1, "ABC", pc, pc, x0 - pc, bc
            if sig_dir == 0 and shape_ok and dc == 1 and pb > x0 and pc > pa and c[i] < ema[i]:
                trig = pc - p.trig_frac * (pc - pb)
                if c[i] < trig and trig - c[i] <= p.max_chase_atr * a and h[i - bars_since + 1:i + 1].max() < pc:
                    sig_dir, setup, stop_piv, tp_base, tp_len, piv_bar = -1, "ABC", pc, pc, pc - x0, bc
        if sig_dir == 0:
            continue

        # ---- filters
        vol_ok = p.min_atr_ratio <= atr_ratio[i] <= p.max_atr_ratio
        adx_ok = (not p.use_adx) or (adx[i] >= p.min_adx)
        sess_ok = (not p.use_session) or (p.session_utc[0] <= minute[i] < p.session_utc[1])
        flow_ok = (i - last_signal_bar >= p.cooldown) and signals_today < p.max_per_day and pos is None and pending is None
        if not (vol_ok and adx_ok and sess_ok and flow_ok):
            continue

        # ---- levels
        entry = c[i]
        struct_sl = stop_piv - sig_dir * p.sl_buf_atr * a
        atr_sl = entry - sig_dir * p.sl_atr * a
        tighter = max(struct_sl, atr_sl) if sig_dir == 1 else min(struct_sl, atr_sl)
        sl = {"structural": struct_sl, "atr": atr_sl}.get(p.sl_mode, tighter)
        if abs(entry - sl) < p.min_sl_atr * a:
            sl = entry - sig_dir * p.min_sl_atr * a
        risk = abs(entry - sl)
        side_ok = sl < entry if sig_dir == 1 else sl > entry
        if not (risk > 0 and side_ok and risk <= p.max_sl_atr * a):
            continue
        tps = []
        for f, rf in zip(p.tp_fib, p.r_floor):
            if p.tp_single_r:
                tps.append(entry + sig_dir * p.tp_single_r * risk)
                continue
            if sig_dir == 1:
                tps.append(max(tp_base + f * tp_len, entry + rf * risk))
            else:
                tps.append(min(tp_base - f * tp_len, entry - rf * risk))
        if sig_dir == 1:
            tps[1] = max(tps[1], tps[0]); tps[2] = max(tps[2], tps[1])
        else:
            tps[1] = min(tps[1], tps[0]); tps[2] = min(tps[2], tps[1])

        last_used_piv, last_signal_bar = piv_bar, i
        signals_today += 1
        pending = Trade(int(ts[i]), 0, sig_dir, setup, entry, sl, *tps, atr=a)
        pending_bar = i + 1 + p.entry_delay

    return pd.DataFrame([asdict(t) for t in trades])


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def stats(r: pd.Series) -> dict:
    if len(r) == 0:
        return dict(n=0)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    eq = r.cumsum()
    dd = (eq.cummax() - eq).max()
    return dict(n=len(r), win=round(100 * (r > 0).mean(), 1), avgR=round(r.mean(), 3),
                totalR=round(r.sum(), 1), PF=round(wins / losses, 2) if losses > 0 else float("inf"),
                maxDD_R=round(dd, 1))


def report(tr: pd.DataFrame, label: str) -> None:
    if tr.empty:
        print(label, "no trades"); return
    tr = tr.copy()
    tr["year"] = pd.to_datetime(tr["entry_ts"], unit="ms").dt.year
    span_years = (tr["entry_ts"].max() - tr["entry_ts"].min()) / (365.25 * 86_400_000)
    print(f"\n=== {label} ===")
    print("net  :", stats(tr["r"]), f"| trades/yr {len(tr) / max(span_years, 1e-9):.0f}")
    print("gross:", stats(tr["r_gross"]))
    print("by setup:", {s: stats(g["r"]) for s, g in tr.groupby("setup")})
    print("by dir  :", {("long" if d == 1 else "short"): stats(g["r"]) for d, g in tr.groupby("direction")})
    print("outcomes:", tr["outcome"].value_counts().to_dict())
    yr = tr.groupby("year")["r"].agg(n="size", totalR="sum", avgR="mean",
                                     win=lambda x: 100 * (x > 0).mean()).round(2)
    print(yr.to_string())


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, index_col="timestamp")
    return df[["open", "high", "low", "close"]]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--spread", type=float, default=0.30)
    ap.add_argument("--slip", type=float, default=0.05)
    args = ap.parse_args()
    params = Params(spread=args.spread, slip=args.slip)
    trades = run(load(args.csv), params)
    report(trades, f"{args.csv} spread={args.spread} slip={args.slip}")
