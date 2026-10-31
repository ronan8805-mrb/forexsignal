"""How many signals per day can the validated RSI(2) dip-buy produce, and at what quality?

Same FIXED rules as validate_mr.py (long only, close > SMA200, RSI(2) < 10, exit RSI(2) > 70,
3 ATR stop, 10-day max). Frequency comes only from adding instruments ("breadth"):
  T1  4 core indices (US500, US Tech 100, Dow 30, Nikkei 225)
  T2  all 24 equity indices + equity ETFs
  T3  T2 + 88 single stocks (US, Europe, Japan)
Evaluation window for per-day statistics: 2019-01-01 .. 2026-10-20 (all tiers have data).
Single-stock caveat: the stock list is today's list (survivorship bias favours long-only).
The random-timing control (shared random dates, matched holding period) is run for the
stock group so the timing edge is judged against the same survivor stocks.
A portfolio cap (max open positions; lowest RSI first on crowded days) shows what is
actually tradable when dozens of stocks dip on the same day.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

import validate_mr as v
from robustness import brief, full_metrics

v.SPLIT_RATIOS = v.SPLIT_RATIOS + (6, 15, 20, 1 / 6, 1 / 15, 1 / 20)
STOCK = dict(spread=0.0, slip=0.0, spread_bps=6.0, slip_bps=1.5, swap_bps=2.0)
START, END = pd.Timestamp("2019-01-01").value // 10**6, pd.Timestamp("2026-10-21").value // 10**6
REPS = 300
rng = np.random.default_rng(31)


def load(path: str, sym: str) -> dict:
    d = v.split_adjust(sym, pd.read_csv(path, index_col="timestamp"))
    d.to_csv("/tmp/_adj.csv")
    orig = v.load_daily.__globals__["pd"].read_csv
    # reuse validate_mr indicator code on the adjusted frame
    c = d["close"]
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)
    pc = c.shift(1)
    tr = np.maximum(d.high - d.low, np.maximum((d.high - pc).abs(), (d.low - pc).abs()))
    return dict(ts=d.index.to_numpy(), o=d.open.to_numpy(), h=d.high.to_numpy(), l=d.low.to_numpy(),
                c=c.to_numpy(), rsi=rsi.to_numpy(), sma=c.rolling(200).mean().to_numpy(),
                atr=tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy())


def trades_for(x: dict, cost: dict, sym: str) -> pd.DataFrame:
    up = (x["c"] > x["sma"]) & ~np.isnan(x["sma"])
    mask = up & (x["rsi"] < v.THR)
    t = pd.DataFrame(v.simulate(x, mask, cost), columns=["entry_ts", "exit_ts", "r", "r_gross", "days"])
    if len(t):
        # RSI at the signal bar (day before entry) for crowd ranking
        pos = np.searchsorted(x["ts"], t.entry_ts.to_numpy()) - 1
        t["rsi"] = x["rsi"][pos]
    t["symbol"] = sym
    return t


def cap_portfolio(t: pd.DataFrame, cap: int) -> pd.DataFrame:
    t = t.sort_values(["entry_ts", "rsi"]).reset_index(drop=True)
    keep, open_exits = [], []
    for row in t.itertuples():
        open_exits = [e for e in open_exits if e >= row.entry_ts]
        if len(open_exits) < cap:
            keep.append(row.Index)
            open_exits.append(row.exit_ts)
    return t.loc[keep]


def per_day(t: pd.DataFrame) -> dict:
    t = t[(t.entry_ts >= START) & (t.entry_ts < END)]
    days = pd.bdate_range(pd.to_datetime(START, unit="ms"), pd.to_datetime(END, unit="ms"))
    d = pd.to_datetime(t.entry_ts, unit="ms").dt.normalize()
    sig = d.value_counts().reindex(days, fill_value=0)
    win = d[t.r > 0].value_counts().reindex(days, fill_value=0)
    return dict(signals_per_day=round(sig.mean(), 2), wins_per_day=round(win.mean(), 2),
                pct_days_with_a_win=round(100 * (win > 0).mean(), 1),
                pct_days_no_signal=round(100 * (sig == 0).mean(), 1), max_signals_one_day=int(sig.max()))


def control_p(objs: dict, real: pd.DataFrame, cost: dict) -> tuple[float, float]:
    all_days = np.unique(np.concatenate([x["ts"] for x in objs.values()]))
    real_mean, means = real.r.mean(), []
    meta = {}
    for s, x in objs.items():
        up = (x["c"] > x["sma"]) & ~np.isnan(x["sma"])
        dens = (up & (x["rsi"] < v.THR)).sum() / max(up.sum(), 1)
        holds = real[real.symbol == s].days.to_numpy()
        meta[s] = (up, dens, holds if len(holds) else np.array([3]))
    for _ in range(REPS):
        u = pd.Series(rng.random(len(all_days)), index=all_days)
        rr = []
        for s, x in objs.items():
            up, dens, holds = meta[s]
            m = up & (u.reindex(x["ts"]).to_numpy() < dens)
            rr += [q[2] for q in v.simulate(x, m, cost, hold=rng.permutation(holds))]
        means.append(np.mean(rr))
    means = np.array(means)
    return float(means.mean()), float((means >= real_mean).mean())


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    eq = {**v.GROUPS["PRIMARY"], **{k: v.IDX for k in ("usa500idxusd", "usatechidxusd", "deuidxeur")}}
    core = ["usa500idxusd", "usatechidxusd", "usa30idxusd", "jpnidxjpy"]
    objs_eq = {s: load(f"data_daily/{s}.csv", s) for s in eq}
    stocks = sorted(f[:-4] for f in os.listdir("data_daily_stocks"))
    objs_st = {s: load(f"data_daily_stocks/{s}.csv", s) for s in stocks}
    t_eq = pd.concat([trades_for(objs_eq[s], eq[s], s) for s in eq], ignore_index=True)
    t_st = pd.concat([trades_for(objs_st[s], STOCK, s) for s in stocks], ignore_index=True)
    print("split adjustments:", [x for x in v.SPLIT_LOG])
    tiers = {"T1 core 4 indices": t_eq[t_eq.symbol.isin(core)],
             "T2 24 indices+ETFs": t_eq,
             "T3 + 88 stocks": pd.concat([t_eq, t_st], ignore_index=True)}
    rows = []
    for name, t in tiers.items():
        w = t[(t.entry_ts >= START) & (t.entry_ts < END)]
        m = full_metrics(w)
        rows.append(dict(tier=name, trades=len(w), win=m["win"], expR=m["expectancy_R"], PF=m["PF"],
                         maxDD=m["maxDD_pct"], **per_day(t)))
        for cap in (10, 20):
            if name.startswith("T3"):
                c = cap_portfolio(t, cap)
                wc = c[(c.entry_ts >= START) & (c.entry_ts < END)]
                mc = full_metrics(wc)
                rows.append(dict(tier=f"{name}, max {cap} open", trades=len(wc), win=mc["win"], expR=mc["expectancy_R"],
                                 PF=mc["PF"], maxDD=mc["maxDD_pct"], **per_day(c)))
    print("\n=== tiers, 2019-2026 (net of costs, 0.5% risk per trade) ===")
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n=== single stocks only (full history), the new instruments ===")
    print("all  :", brief(t_st.r), "| gross", brief(t_st.r_gross))
    for region, suf in (("US", "ususd"), ("Europe", ("deeur", "freur", "eseur", "nleur", "gbgbx", "chchf")), ("Japan", "jpjpy")):
        g = t_st[t_st.symbol.str.endswith(suf)]
        print(f"{region:6s}:", brief(g.r))
    pos = (t_st.groupby("symbol").r.mean() > 0).mean()
    print(f"stocks with positive expectancy: {100 * pos:.0f}%")
    print("by year:", " | ".join(f"{y}: {g.r.mean():+.2f} ({len(g)})" for y, g in t_st.groupby(pd.to_datetime(t_st.entry_ts, unit='ms').dt.year)))
    cm, p = control_p(objs_st, t_st, STOCK)
    print(f"random-timing control on the same stocks ({REPS} runs, shared dates, matched holds): "
          f"mean {cm:+.3f}R vs real {t_st.r.mean():+.3f}R  -> p = {p:.3f}")
    pd.DataFrame(rows).to_csv("out_frequency_tiers.csv", index=False)
    t_st.to_csv("out_frequency_stock_trades.csv", index=False)
