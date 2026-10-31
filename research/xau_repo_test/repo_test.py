"""Independent test of github.com/a1shmuk/xauusd-algo-trader, Phase 1 (the only part its backtest covers).

Rules copied from the repo (xauusd_bot.py live settings = optimizer's best):
  H1 EMA9/EMA21 cross; RSI(14) Wilder: BUY needs 50 < RSI < 65, SELL 35 < RSI < 50; H4 close > EMA50 for BUY
  (< for SELL); session 13-17 (MT5 server hour); all 3 filters required. Entry at the signal bar close +- $0.30 spread.
  SL = tighter of swing(5) -+ $0.50 and 2.0 x ATR(14); TP = 3.0 x ATR. Exits: TP, SL, RSI >= 65 (BUY) / <= 35 (SELL),
  EMA cross against. One trade at a time.
Modes:
  AS_WRITTEN : the repo's own mechanics, including its look-ahead (H4 value joined on the H4 bar OPEN time, swing points
               used before they are confirmed), TP counted first when a bar hits both, flat $0.07 commission.
  FIXED      : H4 trend from the last COMPLETED H4 bar, swing points only after their 5 confirming bars, stop counted
               first when a bar hits both, commission $7 per lot round trip ($0.07/oz).
Results are in R (P&L / planned risk), so position-size choices don't matter.
Data: Dukascopy XAUUSD H1 bid, 2006-10 -> 2026-10 (UTC). Server time = New York time + 7h (GMT+2/+3, the usual MT5 clock).
    python repo_test.py
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

SPREAD, COMM_OZ, SW, BUF = 0.30, 0.07, 5, 0.50
BEST = dict(rsi_ob=65, rsi_os=35, rsi_mid=50, sl=2.0, tp=3.0, s0=13, s1=17, minf=3)
DEFAULT = dict(rsi_ob=70, rsi_os=30, rsi_mid=50, sl=1.5, tp=3.0, s0=8, s1=17, minf=2)   # repo's pre-optimisation
rng = np.random.default_rng(5)


def load(clock: str = "server") -> pd.DataFrame:
    d = pd.read_csv("../data/xauusd/h1.csv")
    utc = pd.to_datetime(d.timestamp, unit="ms", utc=True)
    if clock == "server":
        t = (utc.dt.tz_convert("America/New_York") + pd.Timedelta(hours=7)).dt.tz_localize(None)
    else:
        t = utc.dt.tz_localize(None)
    d.index = pd.DatetimeIndex(t)
    d = d[["open", "high", "low", "close"]]
    d["utc"] = utc.dt.tz_localize(None).to_numpy()
    return d


def indicators(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    c = d.close
    d["ef"], d["es"] = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
    pos = np.where(d.ef > d.es, 1, -1)
    d["bull"] = (pos == 1) & (np.r_[0, pos[:-1]] == -1)
    d["bear"] = (pos == -1) & (np.r_[0, pos[:-1]] == 1)
    dl = c.diff()
    ag = dl.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    al = (-dl.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + ag / al)
    pc = c.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    # H4 bars on the server clock (00/04/08/12/16/20)
    h4 = d[["open", "high", "low", "close"]].resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    h4["bull"] = h4.close > h4.close.ewm(span=50, adjust=False).mean()
    # AS_WRITTEN: joined on H4 open time (sees that H4 bar's close up to 3 hours early)
    d["h4_lookahead"] = h4.bull.reindex(d.index, method="ffill").fillna(False).astype(bool)
    # FIXED: value of the last H4 bar that has CLOSED by the end of this H1 bar
    closed_at = h4.index + pd.Timedelta(hours=4)
    s = pd.Series(h4.bull.to_numpy(), index=closed_at)
    h1_close = d.index + pd.Timedelta(hours=1)
    d["h4_fixed"] = s.reindex(h1_close, method="ffill").fillna(False).astype(bool).to_numpy()
    # swings
    lo, hi = d.low.to_numpy(), d.high.to_numpy()
    n = len(d)
    swl, swh = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(SW, n - SW):
        if all(lo[i] < lo[i - j] for j in range(1, SW + 1)) and all(lo[i] < lo[i + j] for j in range(1, SW + 1)):
            swl[i] = lo[i]
        if all(hi[i] > hi[i - j] for j in range(1, SW + 1)) and all(hi[i] > hi[i + j] for j in range(1, SW + 1)):
            swh[i] = hi[i]
    d["swl_lookahead"] = pd.Series(swl, index=d.index).ffill()      # repo: usable from the swing bar itself
    d["swh_lookahead"] = pd.Series(swh, index=d.index).ffill()
    d["swl_fixed"] = pd.Series(swl, index=d.index).shift(SW).ffill()  # usable only once 5 later bars confirm it
    d["swh_fixed"] = pd.Series(swh, index=d.index).shift(SW).ffill()
    return d


def backtest(d: pd.DataFrame, p: dict, mode: str, entries: np.ndarray | None = None) -> pd.DataFrame:
    """entries: optional array of (bar index, direction) for the random control (filters ignored)."""
    fixed = mode == "FIXED"
    h4 = d["h4_fixed" if fixed else "h4_lookahead"].to_numpy()
    swl = d["swl_fixed" if fixed else "swl_lookahead"].to_numpy()
    swh = d["swh_fixed" if fixed else "swh_lookahead"].to_numpy()
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    rsi, atr, bull, bear = d.rsi.to_numpy(), d.atr.to_numpy(), d.bull.to_numpy(), d.bear.to_numpy()
    hour, wd = d.index.hour.to_numpy(), d.index.weekday.to_numpy()
    utc_year = pd.DatetimeIndex(d.utc).year.to_numpy()
    rand = {} if entries is None else {int(i): int(s) for i, s in entries}
    out, i, n = [], 50, len(d)
    while i < n - 1:
        if entries is None:
            if not (bull[i] or bear[i]):
                i += 1; continue
            s = 1 if bull[i] else -1
            rsi_ok = (p["rsi_mid"] < rsi[i] < p["rsi_ob"]) if s == 1 else (p["rsi_os"] < rsi[i] < p["rsi_mid"])
            mtf_ok = bool(h4[i]) if s == 1 else not bool(h4[i])
            ses_ok = p["s0"] <= hour[i] < p["s1"] and wd[i] < 5
            if int(rsi_ok) + int(mtf_ok) + int(ses_ok) < p["minf"]:
                i += 1; continue
        else:
            if i not in rand:
                i += 1; continue
            s = rand[i]
        e = c[i] + s * SPREAD
        sl = e - s * atr[i] * p["sl"]
        tp = e + s * atr[i] * p["tp"]
        sw = swl[i] if s == 1 else swh[i]
        if not np.isnan(sw):
            sl = max(sw - BUF, sl) if s == 1 else min(sw + BUF, sl)
        risk = abs(e - sl)
        if risk < 0.1 or (s == 1 and sl >= e) or (s == -1 and sl <= e):
            i += 1; continue
        j, px, why = i + 1, None, None
        while j < n:
            hit_sl = l[j] <= sl if s == 1 else h[j] >= sl
            hit_tp = h[j] >= tp if s == 1 else l[j] <= tp
            if hit_sl and hit_tp:
                px, why = (sl, "SL") if fixed else (tp, "TP")
            elif hit_tp:
                px, why = tp, "TP"
            elif hit_sl:
                px, why = (min(sl, o[j]) if s == 1 else max(sl, o[j])) if fixed else sl, "SL"
            elif (s == -1 and rsi[j] <= p["rsi_os"]) or (s == 1 and rsi[j] >= p["rsi_ob"]):
                px, why = c[j], "RSI"
            elif (s == -1 and bull[j]) or (s == 1 and bear[j]):
                px, why = c[j], "EMA"
            if px is not None:
                break
            j += 1
        if px is None:
            break
        # repo: a flat $0.07 per trade on a $100 (1% of $10k) risk = 0.0007R; FIXED: $7/lot = $0.07 per oz
        r = s * (px - e) / risk - (COMM_OZ / risk if fixed else 0.0007)
        out.append((d.index[i], utc_year[i], s, why, j - i, r))
        i = j + 1 if entries is None else j + 1
    return pd.DataFrame(out, columns=["time", "year", "dir", "exit", "bars", "r"])


def stats(t: pd.DataFrame) -> dict:
    if len(t) == 0:
        return dict(n=0)
    r = t.r
    yrs = max((t.time.max() - t.time.min()).days / 365.25, 0.5)
    eq = np.cumsum(r.to_numpy() * 1.0)          # % at 1% risk, additive
    dd = np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq])
    return dict(n=len(t), per_yr=round(len(t) / yrs, 1), win=round(100 * (r > 0).mean(), 1), exp=round(r.mean(), 3),
                PF=round(r[r > 0].sum() / -r[r < 0].sum(), 2) if (r < 0).any() else np.nan,
                ret_pct_1pct_risk=round(r.sum(), 1), maxDD_pct=round(dd, 1))


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    t0 = time.time()
    clocks = {}
    for clock in ("server", "utc"):
        clocks[clock] = indicators(load(clock))
    d = clocks["server"]
    print(f"data {d.utc.min()} -> {d.utc.max()}, {len(d):,} H1 bars  [{time.time() - t0:.0f}s]", flush=True)
    rows, keep = [], {}
    split = pd.Timestamp("2024-01-01")
    for clock, dd_ in clocks.items():
        for pname, p in [("optimised (live)", BEST), ("repo default", DEFAULT)]:
            for mode in ("AS_WRITTEN", "FIXED"):
                t = backtest(dd_, p, mode)
                keep[(clock, pname, mode)] = t
                old, new = t[pd.DatetimeIndex(t.time) < split], t[pd.DatetimeIndex(t.time) >= split]
                for lab, x in [("2006-2023 (never tuned on)", old), ("2024-2026 (their tuning window)", new)]:
                    rows.append(dict(clock=clock, params=pname, mode=mode, period=lab, **stats(x)))
    R = pd.DataFrame(rows)
    print(R.to_string(index=False), flush=True)
    R.to_csv("out_repo_test_summary.csv", index=False)

    # year by year, optimised + FIXED, server clock
    t = keep[("server", "optimised (live)", "FIXED")]
    t.to_csv("out_repo_test_trades_fixed.csv", index=False)
    keep[("server", "optimised (live)", "AS_WRITTEN")].to_csv("out_repo_test_trades_aswritten.csv", index=False)
    print("\nyear by year (optimised settings, FIXED, server clock):")
    print(t.groupby("year").r.agg(n="size", win=lambda x: round(100 * (x > 0).mean(), 1), exp=lambda x: round(x.mean(), 3),
                                  sumR="sum").round(2).T.to_string())

    # random-entry control: same bars-per-session, same directions, same SL/TP/exit construction (FIXED)
    real = t.r.mean()
    hour, wd = d.index.hour.to_numpy(), d.index.weekday.to_numpy()
    pool = np.flatnonzero((hour >= BEST["s0"]) & (hour < BEST["s1"]) & (wd < 5) & (np.arange(len(d)) > 50))
    dirs = t.dir.to_numpy()
    sims = []
    for k in range(300):
        pick = np.sort(rng.choice(pool, size=len(t) * 3, replace=False))
        ent = np.c_[pick, rng.choice(dirs, size=len(pick))]
        rt = backtest(d, BEST, "FIXED", entries=ent)
        sims.append(rt.r.mean())
    sims = np.array(sims)
    print(f"\nrandom-entry control (300 runs, same session/directions/exits): real {real:+.3f}R vs random mean "
          f"{sims.mean():+.3f}R (95th pct {np.percentile(sims, 95):+.3f}), p = {(sims >= real).mean():.3f}")
    pd.DataFrame(dict(random_mean_r=sims)).to_csv("out_repo_test_random.csv", index=False)
    print(f"done in {time.time() - t0:.0f}s")
