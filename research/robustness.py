"""Phase D: robustness tests and the full metric set on walk-forward out-of-sample trades.

Metrics: win rate (+ Wilson 95% CI), expectancy (R), profit factor, avg win / avg loss,
Sharpe / Sortino / Calmar from a daily equity curve at RISK per trade (fixed fractional,
compounded), max drawdown %, longest recovery (days), % positive months, yearly table,
per-instrument and per-regime breakdown, 1,000-run Monte Carlo (trade-order shuffle):
distribution of max DD and final return, probability of ruin (DD >= 50%).
Stress: costs x2, entry delayed 1 bar, zero costs, remove best 5% of trades.

    python robustness.py A          # track A trades from walkforward.py
"""
from __future__ import annotations

import sys
from dataclasses import replace

import numpy as np
import pandas as pd

RISK = 0.005          # 0.5% of equity per trade
rng = np.random.default_rng(11)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def equity_daily(tr: pd.DataFrame) -> pd.Series:
    d = pd.to_datetime(tr["exit_ts"], unit="ms").dt.floor("D")
    daily_r = tr.groupby(d)["r"].sum().sort_index()
    idx = pd.date_range(daily_r.index.min(), daily_r.index.max(), freq="D")
    daily_ret = (daily_r.reindex(idx, fill_value=0.0) * RISK)
    return (1 + daily_ret).cumprod()


def full_metrics(tr: pd.DataFrame) -> dict:
    r = tr["r"]
    n = len(r)
    if n == 0:
        return {"n": 0}
    wins, losses = r[r > 0], r[r <= 0]
    lo, hi = wilson(int((r > 0).sum()), n)
    eq = equity_daily(tr)
    ret = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    peak = eq.cummax()
    dd = 1 - eq / peak
    maxdd = dd.max()
    # longest time under water (days)
    under = dd > 0
    longest, cur = 0, 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    sharpe = np.sqrt(365) * ret.mean() / ret.std() if ret.std() > 0 else np.nan
    downside = ret[ret < 0].std()
    sortino = np.sqrt(365) * ret.mean() / downside if downside > 0 else np.nan
    monthly = eq.resample("ME").last().pct_change().dropna()
    gl = -losses.sum()
    return dict(n=n, win=round(100 * (r > 0).mean(), 1), win_CI95=f"{lo:.1f}-{hi:.1f}",
                expectancy_R=round(r.mean(), 3), PF=round(wins.sum() / gl, 2) if gl > 0 else np.inf,
                avg_win_R=round(wins.mean(), 3) if len(wins) else 0, avg_loss_R=round(losses.mean(), 3) if len(losses) else 0,
                payoff=round(wins.mean() / -losses.mean(), 2) if len(wins) and len(losses) and losses.mean() < 0 else np.nan,
                CAGR_pct=round(100 * cagr, 2), maxDD_pct=round(100 * maxdd, 1),
                sharpe=round(sharpe, 2), sortino=round(sortino, 2),
                calmar=round(cagr / maxdd, 2) if maxdd > 0 else np.nan,
                longest_underwater_days=longest, pos_months_pct=round(100 * (monthly > 0).mean(), 1))


def monte_carlo(r: np.ndarray, runs: int = 1000) -> dict:
    dds, finals = [], []
    for _ in range(runs):
        s = rng.permutation(r)
        eq = np.cumprod(1 + s * RISK)
        peak = np.maximum.accumulate(eq)
        dds.append((1 - eq / peak).max())
        finals.append(eq[-1] - 1)
    dds, finals = np.array(dds), np.array(finals)
    return dict(runs=runs, maxDD_median_pct=round(100 * np.median(dds), 1), maxDD_p95_pct=round(100 * np.percentile(dds, 95), 1),
                final_ret_median_pct=round(100 * np.median(finals), 1), final_ret_p5_pct=round(100 * np.percentile(finals, 5), 1),
                P_final_loss_pct=round(100 * (finals < 0).mean(), 1),
                P_ruin_DD50_pct=round(100 * (dds >= 0.5).mean(), 1), P_DD25_pct=round(100 * (dds >= 0.25).mean(), 1))


def regime_table(tr: pd.DataFrame) -> pd.DataFrame:
    """Per instrument-year: realised-vol tercile and trend-strength tercile (|yearly move| / vol)."""
    from universe import data_path
    rows = []
    for sym, g in tr.groupby("symbol"):
        df = pd.read_csv(data_path(sym), index_col="timestamp", usecols=["timestamp", "close"])
        daily = df["close"].groupby(pd.to_datetime(df.index, unit="ms").floor("D")).last()
        yr = daily.groupby(daily.index.year)
        vol = yr.apply(lambda x: np.log(x).diff().std() * np.sqrt(252))
        trend = yr.apply(lambda x: abs(np.log(x.iloc[-1] / x.iloc[0]))) / vol
        vt = pd.qcut(vol, 3, labels=["low-vol", "mid-vol", "high-vol"])
        tt = pd.qcut(trend, 3, labels=["ranging", "mixed", "trending"])
        y = pd.to_datetime(g["entry_ts"], unit="ms").dt.year
        rows.append(g.assign(vol_regime=y.map(vt).values, trend_regime=y.map(tt).values))
    t = pd.concat(rows)
    crisis = {"GFC 2008-09": ("2008-09-01", "2009-03-31"), "COVID 2020": ("2020-02-15", "2020-05-31"),
              "2022 rates shock": ("2022-02-01", "2022-10-31")}
    et = pd.to_datetime(t["entry_ts"], unit="ms")
    t["crisis"] = "none"
    for k, (a, b) in crisis.items():
        t.loc[(et >= a) & (et <= b), "crisis"] = k
    return t


def brief(r: pd.Series) -> str:
    if len(r) == 0:
        return "n=0"
    gl = -r[r < 0].sum()
    return f"n={len(r)} win={100 * (r > 0).mean():.1f}% exp={r.mean():+.3f}R PF={r[r > 0].sum() / gl if gl > 0 else np.inf:.2f}"


if __name__ == "__main__":
    track = sys.argv[1] if len(sys.argv) > 1 else "A"
    tr = pd.read_csv(f"out_wf_oos_trades_{track}.csv")
    hold = "HOLDOUT 2023-26"
    print(f"=== Track {track}: walk-forward OOS 2013-2022 ===")
    print(full_metrics(tr[tr.window != hold]))
    print(f"=== Track {track}: final HOLDOUT 2023-2026 ===")
    print(full_metrics(tr[tr.window == hold]))
    print("\n=== Monte Carlo (all OOS trades incl. holdout, 1,000 shuffles, 0.5% risk) ===")
    print(monte_carlo(tr["r"].to_numpy()))
    print("\n=== by instrument (all OOS) ===")
    for s, g in tr.groupby("symbol"):
        print(f"  {s:14s} {brief(g['r'])}")
    print("\n=== by year ===")
    for y, g in tr.groupby(pd.to_datetime(tr["entry_ts"], unit="ms").dt.year):
        print(f"  {y} {brief(g['r'])}")
    rt = regime_table(tr)
    for col in ("vol_regime", "trend_regime", "crisis"):
        print(f"\n=== by {col} ===")
        for k, g in rt.groupby(col, observed=True):
            print(f"  {str(k):18s} {brief(g['r'])}")
    print("\n=== stress ===")
    r = tr["r"].sort_values()
    print("  remove best 5% trades :", brief(r.iloc[: int(len(r) * 0.95)]))
    print("  zero costs (gross)    :", brief(tr["r_gross"]))
    # re-run the window-selected settings with doubled costs and a 1-bar entry delay
    from atr_ew_backtest import Params, load, run
    from universe import UNIVERSE, cost_kwargs, data_path
    from walkforward import COMBOS, HOLDOUT, WINDOWS
    sel = pd.read_csv("out_wf_selection.csv")
    sel = sel[sel.track == track]
    for label, kw in (("costs x2", dict(cost_mult=2.0, delay=0)), ("entry delayed 1 bar", dict(cost_mult=1.0, delay=1))):
        res = []
        for sym in UNIVERSE:
            df = load(data_path(sym))
            for (_, _, te0, te1, wl) in WINDOWS + [HOLDOUT]:
                cid = int(sel[sel.window == wl].combo.iloc[0])
                p = replace(Params(**cost_kwargs(sym, kw["cost_mult"])), **COMBOS[cid], entry_delay=kw["delay"])
                t = run(df, p)
                if not t.empty:
                    res.append(t[(t.entry_ts >= te0) & (t.entry_ts < te1)])
        print(f"  {label:22s}:", brief(pd.concat(res)["r"]))
