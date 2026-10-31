"""1-hour version of the RSI(2) dip-buy: pre-registered test (nothing tuned).

Universe: the same 24 equity indices + ETFs as the daily validation.
Rules (fixed before testing), long only, signals on completed H1 bars, fill next H1 open:
  H-A  pure hourly : close > SMA200(H1) and RSI(2,H1) < 10
  H-B  daily trend : previous day's close > SMA200(daily) and RSI(2,H1) < 10
  Exit: RSI(2,H1) > 70 on a bar close, 3 x ATR(14,H1) stop (assumed hit first), 24-bar time stop.
  Entries only during each market's cash session (UTC hours below); ETFs only trade then anyway.
Costs: same bps spread/slippage as the daily test, overnight financing per 22:00 UTC rollover held.
Controls: random entry bars (one random draw per timestamp shared by all instruments, same
density, same trend filter, holding periods matched to the real trades), 300 runs.
Plateau (reported, not selected): RSI threshold {5,10,15} x exit {50,70} for H-A.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import validate_mr as v
from robustness import brief, full_metrics

SESSION = {"usa500idxusd": (13, 20), "usatechidxusd": (13, 20), "usa30idxusd": (13, 20),
           "jpnidxjpy": (0, 6), "gbridxgbp": (7, 15), "deuidxeur": (7, 15),
           "hkgidxhkd": (1, 8), "chiidxusd": (1, 8)}           # ETFs: all bars (data exists only in session)
K_STOP, MAX_BARS, REPS = 3.0, 24, 300
rng = np.random.default_rng(99)
EQ = {**v.GROUPS["PRIMARY"], **{k: v.IDX for k in ("usa500idxusd", "usatechidxusd", "deuidxeur")}}


def rsi2(c: pd.Series) -> np.ndarray:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()


def load(sym: str) -> dict:
    h = pd.read_csv(f"data/{sym}/h1.csv", index_col="timestamp")[["open", "high", "low", "close"]]
    h = v.split_adjust(sym, h)
    c = h.close
    pc = c.shift(1)
    tr = np.maximum(h.high - h.low, np.maximum((h.high - pc).abs(), (h.low - pc).abs()))
    ts = h.index.to_numpy()
    # daily trend known at the START of each hour: yesterday's close vs yesterday's SMA200(daily)
    d = pd.read_csv(f"data_daily/{sym}.csv", index_col="timestamp")
    d = v.split_adjust(sym, d)
    dtrend = (d.close > d.close.rolling(200).mean()).shift(1)        # value usable during day t
    day = ts // 86_400_000 * 86_400_000
    dt = pd.Series(dtrend.values, index=d.index).reindex(day).fillna(False).to_numpy(dtype=bool)
    hour = (ts // 3_600_000) % 24
    lo, hi = SESSION.get(sym, (0, 24))
    return dict(ts=ts, o=h.open.to_numpy(), h=h.high.to_numpy(), l=h.low.to_numpy(), c=c.to_numpy(),
                rsi=rsi2(c), sma=c.rolling(200).mean().to_numpy(), atr=tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy(),
                dtrend=dt, insess=(hour >= lo) & (hour < hi))


def simulate(x: dict, mask: np.ndarray, cost: dict, exit_rsi: float = 70, hold: np.ndarray | None = None) -> list:
    o, h, l, c, rsi, atr, ts = x["o"], x["h"], x["l"], x["c"], x["rsi"], x["atr"], x["ts"]
    n, out, i, k = len(o), [], 201, 0
    while i < n - 1:
        if not mask[i]:
            i += 1
            continue
        e = i + 1
        fill = o[e] * (1 + (cost["spread_bps"] + cost["slip_bps"]) * 1e-4) + cost["spread"] + cost["slip"]
        risk = K_STOP * atr[i]
        stop = fill - risk
        tgt = int(hold[k % len(hold)]) if hold is not None else None
        k += 1
        j, px = e, None
        while j < n:
            slj = o[j] * cost["slip_bps"] * 1e-4 + cost["slip"]
            if l[j] <= stop:
                px = min(stop, o[j]) - slj; break
            bars = j - e + 1
            if (hold is None and (rsi[j] > exit_rsi or bars >= MAX_BARS)) or (hold is not None and bars >= tgt):
                px = c[j] - slj; break
            j += 1
        if px is None:
            break
        nights = int((ts[j] + 7_200_000) // 86_400_000 - (ts[e] + 7_200_000) // 86_400_000)
        r = (px - fill - nights * fill * cost["swap_bps"] * 1e-4) / risk
        out.append((int(ts[e]), int(ts[j]) + 3_599_000, r, j - e + 1))
        i = j + 1
    return out


def run(objs: dict, variant: str, thr: float = 10, exit_rsi: float = 70) -> pd.DataFrame:
    rows = []
    for s, x in objs.items():
        trend = (x["c"] > x["sma"]) if variant == "H-A" else x["dtrend"]
        m = trend & x["insess"] & (x["rsi"] < thr)
        t = pd.DataFrame(simulate(x, m, EQ[s], exit_rsi), columns=["entry_ts", "exit_ts", "r", "bars"])
        rows.append(t.assign(symbol=s))
    return pd.concat(rows, ignore_index=True)


def control(objs: dict, real: pd.DataFrame, variant: str) -> tuple[float, float]:
    all_ts = np.unique(np.concatenate([x["ts"] for x in objs.values()]))
    meta = {}
    for s, x in objs.items():
        trend = (x["c"] > x["sma"]) if variant == "H-A" else x["dtrend"]
        elig = trend & x["insess"]
        dens = (elig & (x["rsi"] < 10)).sum() / max(elig.sum(), 1)
        holds = real[real.symbol == s].bars.to_numpy()
        meta[s] = (elig, dens, holds if len(holds) else np.array([3]))
    means = []
    for _ in range(REPS):
        u = pd.Series(rng.random(len(all_ts)), index=all_ts)
        rr = []
        for s, x in objs.items():
            elig, dens, holds = meta[s]
            m = elig & (u.reindex(x["ts"]).to_numpy() < dens)
            rr += [q[2] for q in simulate(x, m, EQ[s], hold=rng.permutation(holds))]
        means.append(np.mean(rr))
    means = np.array(means)
    return float(means.mean()), float((means >= real.r.mean()).mean())


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    objs = {s: load(s) for s in EQ}
    start = pd.Timestamp("2019-01-01").value // 10**6
    for variant in ("H-A", "H-B"):
        t = run(objs, variant)
        m = full_metrics(t)
        w = t.groupby(t.entry_ts // (7 * 86_400_000)).r.sum()
        tw = w.mean() / (w.std(ddof=1) / np.sqrt(len(w)))
        recent = t[t.entry_ts >= start]
        days = len(pd.bdate_range("2019-01-01", "2026-10-20"))
        print(f"\n######## {variant} ########")
        print("pooled:", {k: m[k] for k in ("n", "win", "win_CI95", "expectancy_R", "PF", "avg_win_R", "avg_loss_R", "maxDD_pct")})
        print(f"weekly-clustered t {tw:.2f} | signals/day (2019-26) {len(recent) / days:.2f} | wins/day {(recent.r > 0).sum() / days:.2f}"
              f" | avg hold {t.bars.mean():.1f} bars")
        ps = t.groupby("symbol").r.agg(["size", "mean"])
        print(f"positive instruments {(ps['mean'] > 0).sum()}/{len(ps)}:",
              ", ".join(f"{s.replace('ususd', '').replace('idx', '')} {r['mean']:+.2f}" for s, r in ps.iterrows()))
        print("by year:", " | ".join(f"{y}: {g.r.mean():+.2f}" for y, g in t.groupby(pd.to_datetime(t.entry_ts, unit='ms').dt.year)))
        cm, p = control(objs, t, variant)
        print(f"random-timing control: mean {cm:+.3f}R vs real {t.r.mean():+.3f}R -> p = {p:.3f}")
        t.to_csv(f"out_hourly_{variant}.csv", index=False)
    print("\nplateau H-A (all instruments, full history):")
    for thr in (5, 10, 15):
        print("  " + " | ".join(f"thr {thr} exit {ex}: {brief(run(objs, 'H-A', thr, ex).r)}" for ex in (50, 70)))
