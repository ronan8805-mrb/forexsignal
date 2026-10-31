"""Pre-registered improvement test for the repo's gold strategy (plan: IMPROVE_PREREG.md).
    python improve.py
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

import repo_test as RT

COST_BPS = 1.5 + 0.35 + 0.5          # spread + commission + slippage, round trip
SWAP_BPS = 1.5                       # per night held
K_TRAIL = 3.0
DEV_END, VAL_END = pd.Timestamp("2017-01-01"), pd.Timestamp("2023-01-01")
rng = np.random.default_rng(11)


def atr(df: pd.DataFrame, n: int) -> np.ndarray:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean().to_numpy()


def rsi(c: pd.Series, n: int = 14) -> np.ndarray:
    dl = c.diff()
    ag = dl.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    al = (-dl.clip(upper=0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return (100 - 100 / (1 + ag / al)).to_numpy()


def resample(h1: pd.DataFrame, rule: str, min_bars: int) -> pd.DataFrame:
    g = h1.resample(rule, label="left", closed="left")
    df = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    df["nb"] = g.close.count()
    df["utc"] = g.utc.first()
    return df[df.nb >= min_bars].dropna()


def engine(f: dict, sig: np.ndarray, init_stop, exit_long: np.ndarray, exit_short: np.ndarray,
           cost_mult: float = 1.0) -> pd.DataFrame:
    o, h, l, c, a, t = f["o"], f["h"], f["l"], f["c"], f["atr"], f["utc"]
    n, out, i = len(o), [], 0
    while i < n - 1:
        d = int(sig[i])
        if d == 0 or np.isnan(a[i]):
            i += 1; continue
        e = i + 1
        entry = o[e]
        stop = init_stop(i, d, entry)
        risk = d * (entry - stop)
        if not np.isfinite(risk) or risk <= 0:
            i += 1; continue
        best, j, px = entry, e, None
        while j < n:
            if d == 1 and l[j] <= stop:
                px = min(stop, o[j]); break
            if d == -1 and h[j] >= stop:
                px = max(stop, o[j]); break
            best = max(best, c[j]) if d == 1 else min(best, c[j])
            if not np.isnan(a[j]):
                tr_ = best - d * K_TRAIL * a[j]
                stop = max(stop, tr_) if d == 1 else min(stop, tr_)
            if (d == 1 and exit_long[j]) or (d == -1 and exit_short[j]):
                if j + 1 < n:
                    j += 1; px = o[j]
                break
            j += 1
        if px is None:
            break
        nights = max(0, (pd.Timestamp(t[j]) - pd.Timestamp(t[e])).days)
        cost = entry * (COST_BPS + nights * SWAP_BPS) * 1e-4 * cost_mult
        out.append((pd.Timestamp(t[i]), d, (d * (px - entry) - cost) / risk, j - e))
        i = j
    return pd.DataFrame(out, columns=["time", "dir", "r", "bars"])


def frames():
    h1 = RT.load("server")
    ind = RT.indicators(h1)
    # --- H1 (V1): repo signal, repo stop
    F1 = dict(o=h1.open.to_numpy(), h=h1.high.to_numpy(), l=h1.low.to_numpy(), c=h1.close.to_numpy(),
              atr=ind.atr.to_numpy(), utc=h1.utc.to_numpy())
    p = RT.BEST
    hour, wd = h1.index.hour.to_numpy(), h1.index.weekday.to_numpy()
    r_, h4b = ind.rsi.to_numpy(), ind.h4_fixed.to_numpy()
    ses = (hour >= p["s0"]) & (hour < p["s1"]) & (wd < 5)
    long1 = ind.bull.to_numpy() & (r_ > p["rsi_mid"]) & (r_ < p["rsi_ob"]) & h4b & ses
    short1 = ind.bear.to_numpy() & (r_ < p["rsi_mid"]) & (r_ > p["rsi_os"]) & ~h4b & ses
    F1["sig"] = np.where(long1, 1, np.where(short1, -1, 0))
    swl, swh, at1 = ind.swl_fixed.to_numpy(), ind.swh_fixed.to_numpy(), ind.atr.to_numpy()
    def stop1(i, d, entry):
        s = entry - d * 2.0 * at1[i]
        sw = swl[i] if d == 1 else swh[i]
        if not np.isnan(sw):
            s = max(sw - 0.5, s) if d == 1 else min(sw + 0.5, s)
        return s
    F1["stop"], F1["xl"], F1["xs"] = stop1, ind.bear.to_numpy(), ind.bull.to_numpy()
    F1["pool"] = np.flatnonzero(ses & (np.arange(len(h1)) > 60))
    # --- daily (server clock) for V2's trend and V3/V4
    d1 = resample(h1, "1D", 4)
    d1["ema50"] = d1.close.ewm(span=50, adjust=False).mean()
    # --- H4 (V2)
    h4 = resample(h1, "4h", 2)
    ef, es = h4.close.ewm(span=9, adjust=False).mean(), h4.close.ewm(span=21, adjust=False).mean()
    pos = np.where(ef > es, 1, -1)
    bull, bear = (pos == 1) & (np.r_[0, pos[:-1]] == -1), (pos == -1) & (np.r_[0, pos[:-1]] == 1)
    r4 = rsi(h4.close)
    dtrend = pd.Series((d1.close > d1.ema50).to_numpy(), index=d1.index + pd.Timedelta(days=1))
    up = dtrend.reindex(h4.index + pd.Timedelta(hours=4), method="ffill").fillna(False).astype(bool).to_numpy()
    long2 = bull & (r4 > 50) & (r4 < 65) & up
    short2 = bear & (r4 < 50) & (r4 > 35) & ~up
    a4 = atr(h4, 14)
    F2 = dict(o=h4.open.to_numpy(), h=h4.high.to_numpy(), l=h4.low.to_numpy(), c=h4.close.to_numpy(), atr=a4,
              utc=h4.utc.to_numpy(), sig=np.where(long2, 1, np.where(short2, -1, 0)),
              stop=lambda i, d, entry: entry - d * 2.0 * a4[i], xl=bear, xs=bull,
              pool=np.flatnonzero(np.arange(len(h4)) > 60))
    # --- daily breakout (V3, V4)
    hh50, ll50 = d1.high.rolling(50).max().shift(1), d1.low.rolling(50).min().shift(1)
    hh20, ll20 = d1.high.rolling(20).max().shift(1), d1.low.rolling(20).min().shift(1)
    ad = atr(d1, 20)
    long3, short3 = (d1.close > hh50).to_numpy(), (d1.close < ll50).to_numpy()
    base = dict(o=d1.open.to_numpy(), h=d1.high.to_numpy(), l=d1.low.to_numpy(), c=d1.close.to_numpy(), atr=ad,
                utc=d1.utc.to_numpy(), stop=lambda i, d, entry: entry - d * 2.0 * ad[i],
                xl=(d1.close < ll20).to_numpy(), xs=(d1.close > hh20).to_numpy(),
                pool=np.flatnonzero(np.arange(len(d1)) > 60))
    F3 = dict(base, sig=np.where(long3, 1, np.where(short3, -1, 0)))
    F4 = dict(base, sig=np.where(long3, 1, 0))
    return {"V1 repo + trailing exit (H1)": F1, "V2 H4 + daily trend": F2, "V3 daily 50/20 breakout": F3,
            "V4 daily breakout, long only": F4}


def st(t: pd.DataFrame) -> dict:
    if len(t) == 0:
        return dict(n=0, win=np.nan, exp=np.nan, PF=np.nan)
    r = t.r
    return dict(n=len(t), win=round(100 * (r > 0).mean(), 1), exp=round(r.mean(), 3),
                PF=round(r[r > 0].sum() / -r[r < 0].sum(), 2) if (r < 0).any() else np.nan, sumR=round(r.sum(), 1))


def parts(t):
    return {"DEV 2006-16": t[t.time < DEV_END], "VAL 2017-22": t[(t.time >= DEV_END) & (t.time < VAL_END)],
            "HOLDOUT 2023-26": t[t.time >= VAL_END], "FULL": t}


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    t0 = time.time()
    F = frames()
    print(f"frames built [{time.time() - t0:.0f}s]", flush=True)
    rows, trades = [], {}
    # V0 reference (repo fixed), from the first test's trade list
    v0 = pd.read_csv("out_repo_test_trades_fixed.csv", parse_dates=["time"])
    v0["time"] = v0.time.dt.tz_localize(None) if v0.time.dt.tz is not None else v0.time
    trades["V0 repo fixed (reference)"] = v0
    for name, f in F.items():
        trades[name] = engine(f, f["sig"], f["stop"], f["xl"], f["xs"])
    for name, t in trades.items():
        for per, x in parts(t).items():
            rows.append(dict(variant=name, period=per, **st(x)))
    R = pd.DataFrame(rows)
    print(R.pivot_table(index="variant", columns="period", values="exp", sort=False).to_string())
    print("\n" + R.to_string(index=False))
    R.to_csv("out_improve_summary.csv", index=False)

    # selection on DEV only
    cand = R[(R.period == "DEV 2006-16") & (R.n >= 50) & (~R.variant.str.startswith("V0"))]
    pick = cand.sort_values("exp", ascending=False).iloc[0].variant
    print(f"\nSELECTED on development only: {pick}  (dev exp {cand.set_index('variant').loc[pick, 'exp']:+.3f})")
    t = trades[pick]
    for per, x in parts(t).items():
        print(f"  {per:16s}", st(x), "| long", st(x[x.dir == 1]).get("exp"), "short", st(x[x.dir == -1]).get("exp"))
    f = F[pick]
    t2 = engine(f, f["sig"], f["stop"], f["xl"], f["xs"], cost_mult=2.0)
    print("  2x costs FULL:", st(t2), "| HOLDOUT:", st(t2[t2.time >= VAL_END]))
    print("  by year:", t.groupby(t.time.dt.year).r.sum().round(1).to_dict())
    t.to_csv("out_improve_trades_selected.csv", index=False)

    # random-entry control: same frame, same direction mix, same stops and exits, ~same trade count
    dirs, real = t.dir.to_numpy(), t.r.mean()
    sims = []
    pool = f["pool"]
    for _ in range(300):
        sig = np.zeros(len(f["o"]), int)
        k = min(len(pool), len(t) * 4)
        idx = rng.choice(pool, size=k, replace=False)
        sig[idx] = rng.choice(dirs, size=k)
        rt = engine(f, sig, f["stop"], f["xl"], f["xs"])
        sims.append(rt.r.mean())
    sims = np.array(sims)
    p = float((sims >= real).mean())
    print(f"\nrandom-entry control (300 runs): real {real:+.3f}R vs random mean {sims.mean():+.3f}R "
          f"(95th pct {np.percentile(sims, 95):+.3f}), p = {p:.3f}")
    # same control for every variant (diagnostic)
    for name, ff in F.items():
        if name == pick:
            continue
        tt = trades[name]; ss = []
        for _ in range(100):
            sig = np.zeros(len(ff["o"]), int)
            k = min(len(ff["pool"]), len(tt) * 4)
            idx = rng.choice(ff["pool"], size=k, replace=False)
            sig[idx] = rng.choice(tt.dir.to_numpy(), size=k)
            ss.append(engine(ff, sig, ff["stop"], ff["xl"], ff["xs"]).r.mean())
        print(f"  (diagnostic) {name}: real {tt.r.mean():+.3f} vs random {np.mean(ss):+.3f}, p = {(np.array(ss) >= tt.r.mean()).mean():.2f}")
    print(f"done in {time.time() - t0:.0f}s")
