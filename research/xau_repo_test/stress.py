"""Stress tests of the selected improved gold strategy V2 (H4 EMA9/21 cross + RSI band + daily EMA50 trend,
2xATR initial stop, 3xATR trailing stop, exit on opposite cross). Rules are NOT changed here.
    python stress.py
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

import improve as I

rng = np.random.default_rng(2026)
VAL_END = pd.Timestamp("2023-01-01")
# round-trip bps (spread + commission + slippage), financing bps per night
COSTS = {"fx": (1.3, 0.5), "metal": (2.35, 1.5), "index": (2.5, 2.0), "crypto": (13.0, 5.0), "etf": (4.0, 2.0)}


def kind(sym: str) -> str:
    if sym in ("xauusd", "xagusd"):
        return "metal"
    if sym in ("btcusd", "ethusd"):
        return "crypto"
    if "idx" in sym:
        return "index"
    if sym.endswith("ususd"):
        return "etf"
    return "fx"


def load(sym: str) -> pd.DataFrame:
    d = pd.read_csv(f"../data/{sym}/h1.csv")
    utc = pd.to_datetime(d.timestamp, unit="ms", utc=True)
    d.index = pd.DatetimeIndex((utc.dt.tz_convert("America/New_York") + pd.Timedelta(hours=7)).dt.tz_localize(None))
    d = d[["open", "high", "low", "close"]].copy()
    d["utc"] = utc.dt.tz_localize(None).to_numpy()
    return d[~d.index.duplicated()]


def v2_frame(h1: pd.DataFrame, init_k: float = 2.0) -> dict:
    d1 = I.resample(h1, "1D", 4)
    d1["ema50"] = d1.close.ewm(span=50, adjust=False).mean()
    h4 = I.resample(h1, "4h", 2)
    ef, es = h4.close.ewm(span=9, adjust=False).mean(), h4.close.ewm(span=21, adjust=False).mean()
    pos = np.where(ef > es, 1, -1)
    bull, bear = (pos == 1) & (np.r_[0, pos[:-1]] == -1), (pos == -1) & (np.r_[0, pos[:-1]] == 1)
    r4 = I.rsi(h4.close)
    dtrend = pd.Series((d1.close > d1.ema50).to_numpy(), index=d1.index + pd.Timedelta(days=1))
    up = dtrend.reindex(h4.index + pd.Timedelta(hours=4), method="ffill").fillna(False).astype(bool).to_numpy()
    sig = np.where(bull & (r4 > 50) & (r4 < 65) & up, 1, np.where(bear & (r4 < 50) & (r4 > 35) & ~up, -1, 0))
    a4 = I.atr(h4, 14)
    return dict(o=h4.open.to_numpy(), h=h4.high.to_numpy(), l=h4.low.to_numpy(), c=h4.close.to_numpy(), atr=a4,
                utc=h4.utc.to_numpy(), sig=sig, xl=bear, xs=bull, init_k=init_k,
                pool=np.flatnonzero(np.arange(len(h4)) > 60))


def run(f: dict, sig=None, cost=(I.COST_BPS, I.SWAP_BPS), cost_mult=1.0, trail=3.0, init_k=None,
        stop_slip_atr=0.0, delay=0) -> pd.DataFrame:
    """Same mechanics as improve.engine, with extra stress knobs."""
    o, h, l, c, a, t = f["o"], f["h"], f["l"], f["c"], f["atr"], f["utc"]
    sig = f["sig"] if sig is None else sig
    if delay:
        sig = np.r_[np.zeros(delay, int), sig[:-delay]]
    k0 = f["init_k"] if init_k is None else init_k
    n, out, i = len(o), [], 0
    while i < n - 1:
        d = int(sig[i])
        if d == 0 or np.isnan(a[i]):
            i += 1; continue
        e = i + 1; entry = o[e]
        stop = entry - d * k0 * a[i - delay if i - delay >= 0 else i]
        risk = d * (entry - stop)
        if not np.isfinite(risk) or risk <= 0:
            i += 1; continue
        best, j, px = entry, e, None
        while j < n:
            slip = stop_slip_atr * (a[j] if not np.isnan(a[j]) else 0)
            if d == 1 and l[j] <= stop:
                px = min(stop, o[j]) - slip; break
            if d == -1 and h[j] >= stop:
                px = max(stop, o[j]) + slip; break
            best = max(best, c[j]) if d == 1 else min(best, c[j])
            if not np.isnan(a[j]):
                tr_ = best - d * trail * a[j]
                stop = max(stop, tr_) if d == 1 else min(stop, tr_)
            if (d == 1 and f["xl"][j]) or (d == -1 and f["xs"][j]):
                if j + 1 < n:
                    j += 1; px = o[j]
                break
            j += 1
        if px is None:
            break
        nights = max(0, (pd.Timestamp(t[j]) - pd.Timestamp(t[e])).days)
        cst = entry * (cost[0] + nights * cost[1]) * 1e-4 * cost_mult
        out.append((pd.Timestamp(t[i]), d, (d * (px - entry) - cst) / risk))
        i = j
    return pd.DataFrame(out, columns=["time", "dir", "r"])


def st(t):
    if len(t) == 0:
        return dict(n=0, exp=np.nan, PF=np.nan, win=np.nan)
    r = t.r
    return dict(n=len(t), win=round(100 * (r > 0).mean(), 1), exp=round(r.mean(), 3),
                PF=round(r[r > 0].sum() / -r[r < 0].sum(), 2) if (r < 0).any() else np.nan)


def rand_p(f, t, runs=100, cost=(I.COST_BPS, I.SWAP_BPS)):
    sims = []
    for _ in range(runs):
        sig = np.zeros(len(f["o"]), int)
        k = min(len(f["pool"]), max(len(t), 1) * 4)
        idx = rng.choice(f["pool"], size=k, replace=False)
        sig[idx] = rng.choice(t.dir.to_numpy(), size=k) if len(t) else 1
        sims.append(run(f, sig=sig, cost=cost).r.mean())
    sims = np.array(sims)
    return round(float(sims.mean()), 3), round(float((sims >= t.r.mean()).mean()), 3)


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    t0 = time.time()
    gold = v2_frame(load("xauusd"))
    base = run(gold)
    print(f"V2 gold baseline: {st(base)}  [{time.time() - t0:.0f}s]")

    print("\n=== S1 cost & execution stress (gold) ===")
    for lab, kw in [("baseline", {}), ("2x costs", dict(cost_mult=2)), ("3x costs", dict(cost_mult=3)),
                    ("entry one H4 bar late", dict(delay=1)), ("stops slip 0.25 ATR", dict(stop_slip_atr=0.25)),
                    ("3x costs + late + slip", dict(cost_mult=3, delay=1, stop_slip_atr=0.25))]:
        t = run(gold, **kw)
        print(f"  {lab:26s} full {st(t)} | holdout exp {t[t.time >= VAL_END].r.mean():+.3f}")

    print("\n=== S2 Monte Carlo (10,000 reshuffles / bootstraps of the 532 gold trades, 1% risk per trade) ===")
    r = base.r.to_numpy()
    dds, tot1y = [], []
    for _ in range(10000):
        x = rng.permutation(r)
        cum = np.cumsum(x); dds.append((np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max())
        tot1y.append(rng.choice(r, size=27, replace=True).sum())          # ~27 trades a year
    dds, tot1y = np.array(dds), np.array(tot1y)
    print(f"  max drawdown: median {np.median(dds):.1f}%, 95th pct {np.percentile(dds, 95):.1f}%, worst {dds.max():.1f}%")
    print(f"  one year (27 trades): median {np.median(tot1y):+.1f}%, P(losing year) {(tot1y < 0).mean():.0%}, "
          f"5th pct {np.percentile(tot1y, 5):+.1f}%")

    print("\n=== S3 gold regimes and crashes ===")
    phases = [("2006-11 bull", "2006-01-01", "2011-09-01"), ("2011-15 bear", "2011-09-01", "2016-01-01"),
              ("2016-18 range", "2016-01-01", "2019-01-01"), ("2019-22 bull/chop", "2019-01-01", "2023-01-01"),
              ("2023-26 bull", "2023-01-01", "2027-01-01")]
    for lab, a, b in phases:
        x = base[(base.time >= a) & (base.time < b)]
        print(f"  {lab:20s} {st(x)} | long {x[x.dir == 1].r.mean():+.3f} short {x[x.dir == -1].r.mean():+.3f}")
    for lab, a, b in [("2008 crash", "2008-07-15", "2008-11-30"), ("Apr 2013 crash", "2013-04-01", "2013-07-31"),
                      ("Mar 2020", "2020-02-15", "2020-04-30"), ("2022 hikes", "2022-03-01", "2022-11-30")]:
        x = base[(base.time >= a) & (base.time < b)]
        print(f"  {lab:20s} trades {len(x)}, total {x.r.sum():+.1f}R, worst {x.r.min() if len(x) else np.nan:+.2f}R")

    print("\n=== S4 walk-forward re-tuning: does picking settings from the past 4 years beat fixed V2? ===")
    grid = [(tr, k) for tr in (2.0, 3.0, 4.0) for k in (1.5, 2.0, 3.0)]
    T = {g: run(gold, trail=g[0], init_k=g[1]) for g in grid}
    wf, fixed, chosen = [], [], []
    for y in range(2011, 2027):
        past = {g: t[(t.time.dt.year >= y - 4) & (t.time.dt.year < y)].r.mean() for g, t in T.items()}
        g = max(past, key=past.get)
        chosen.append(g)
        wf.append(T[g][T[g].time.dt.year == y]); fixed.append(T[(3.0, 2.0)][T[(3.0, 2.0)].time.dt.year == y])
    wf, fixed = pd.concat(wf), pd.concat(fixed)
    hind = max(grid, key=lambda g: T[g][T[g].time.dt.year >= 2011].r.mean())
    print(f"  2011-26 walk-forward (re-tuned yearly): {st(wf)} | fixed V2: {st(fixed)} | "
          f"best-in-hindsight {hind}: exp {T[hind][T[hind].time.dt.year >= 2011].r.mean():+.3f}")
    print("  settings chosen each year:", [f"{y}:{g[0]}/{g[1]}" for y, g in zip(range(2011, 2027), chosen)])

    print("\n=== S5 same rules, unchanged, on every other market with hourly data ===")
    rows = []
    for sym in ["xagusd", "eurusd", "gbpusd", "usdjpy", "audusd", "usdchf", "usdcad", "nzdusd",
                "usa500idxusd", "usatechidxusd", "usa30idxusd", "deuidxeur", "gbridxgbp", "jpnidxjpy",
                "hkgidxhkd", "chiidxusd", "btcusd", "ethusd", "spyususd", "qqqususd", "diaususd", "iwmususd",
                "dvyususd", "eemususd", "efaususd", "iyrususd", "xlfususd", "xliususd", "xlkususd", "xleususd",
                "xlvususd", "smhususd", "arkqususd", "jetsususd"]:
        f = v2_frame(load(sym))
        cost = COSTS[kind(sym)]
        t = run(f, cost=cost)
        if len(t) < 5:
            rows.append(dict(market=sym, kind=kind(sym), n=len(t))); continue
        rm, p = rand_p(f, t, runs=60, cost=cost) if len(t) >= 20 else (np.nan, np.nan)
        rows.append(dict(market=sym, kind=kind(sym), first=str(t.time.min().date()), **st(t),
                         hold_exp=round(t[t.time >= VAL_END].r.mean(), 3), long=round(t[t.dir == 1].r.mean(), 3),
                         short=round(t[t.dir == -1].r.mean(), 3), random_exp=rm, p=p))
    S = pd.DataFrame(rows)
    print(S.to_string(index=False))
    S.to_csv("out_stress_markets.csv", index=False)
    v = S.dropna(subset=["exp"])
    print(f"\n  positive on {(v.exp > 0).sum()} of {len(v)} markets; by kind:",
          v.groupby("kind").exp.agg(["count", "mean", lambda x: (x > 0).sum()]).round(3).to_dict("index"))
    print(f"done in {time.time() - t0:.0f}s")
