"""Robustness for the mean-reversion alternative (scope idx_metals_long, both tracks):
full metrics, Monte Carlo, per-instrument/year/regime, significance (t-stat),
stress (costs x2, 1-day delay, drop best 5%), plateau around the chosen settings,
and a control: random long entries in the same uptrend filter with the same exits."""
import sys
import numpy as np
import pandas as pd
from meanrev import COMBOS, GRID, HOLDOUT, TESTS, SCOPES, daily_bars, run_mr, agg, rsi_wilder
from robustness import brief, full_metrics, monte_carlo, regime_table

scope = sys.argv[1] if len(sys.argv) > 1 else "idx_metals_long"
track = sys.argv[2] if len(sys.argv) > 2 else "B"
syms, dirs = SCOPES[scope]
tr = pd.read_csv(f"out_mr_oos_{scope}_{track}.csv")
sel = pd.read_csv("out_mr_selection.csv")
sel = sel[(sel.scope == scope) & (sel.track == track)]
pd.set_option("display.width", 220)
print(f"### {scope} track {track}")
print("WF OOS 2015-22 :", full_metrics(tr[tr.window != HOLDOUT[2]]))
print("HOLDOUT 2023-26:", full_metrics(tr[tr.window == HOLDOUT[2]]))
print("ALL OOS        :", full_metrics(tr))
r = tr["r"].to_numpy()
t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
print(f"t-stat of mean R (all OOS): {t:.2f}  (|t|>2 ~ 95% significance; note: chosen from 18 settings x 3 scopes)")
print("Monte Carlo:", monte_carlo(r))
print("by instrument:"); [print(f"   {s:14s} {brief(g['r'])}") for s, g in tr.groupby("symbol")]
print("by year:"); [print(f"   {y} {brief(g['r'])}") for y, g in tr.groupby(pd.to_datetime(tr.entry_ts, unit='ms').dt.year)]
rt = regime_table(tr)
for col in ("vol_regime", "trend_regime", "crisis"):
    print(f"by {col}:"); [print(f"   {str(k):18s} {brief(g['r'])}") for k, g in rt.groupby(col, observed=True)]
dailies = {s: daily_bars(s) for s in syms}
def rerun(**kw):
    out = []
    for (te0, te1, wl) in TESTS + [HOLDOUT]:
        cb = {k: sel[sel.window == wl][k].iloc[0] for k in GRID}
        cb["k_stop"] = float(cb["k_stop"]); cb["thr"] = float(cb["thr"])
        for s, d in dailies.items():
            x = run_mr(d, s, **cb, **kw)
            if not x.empty:
                x = x[x.direction.isin(dirs)]
                out.append(x[(x.entry_ts >= te0) & (x.entry_ts < te1)])
    return pd.concat(out)
print("stress:")
print("   costs x2          :", brief(rerun(cost_mult=2.0)["r"]))
print("   costs x3          :", brief(rerun(cost_mult=3.0)["r"]))
print("   entry delayed 1 d :", brief(rerun(delay=1)["r"]))
rs = np.sort(r); print("   drop best 5%      :", brief(pd.Series(rs[: int(len(rs) * 0.95)])))
print("   zero costs        :", brief(tr["r_gross"]))
# plateau: whole-period (2015-2026 OOS span) result of every setting in the grid for this scope
g = pd.read_pickle("out_mr_grid.pkl")
g = g[g.symbol.isin(syms) & g.direction.isin(dirs) & (g.entry_ts >= TESTS[0][0])]
pl = pd.DataFrame([dict(**COMBOS[c], **{k: round(v, 3) for k, v in agg(x["r"]).items()}) for c, x in g.groupby("combo")])
print("plateau (all 18 settings, 2015-2026):"); print(pl.sort_values(["k_stop", "exit_rule", "thr"]).to_string(index=False))
# control: same filter (close>SMA200), same exits, but RANDOM entry days instead of RSI(2) dips
rng = np.random.default_rng(3)
ctrl = []
for s, d in dailies.items():
    d = d.copy()
    for _ in range(20):
        rnd = d.copy()
        # fake an RSI series that is 'oversold' on random uptrend days at the same frequency as real signals
        real = (rsi_wilder(d.close, 2) < 5)
        p = real.mean()
        mask = rng.random(len(d)) < p
        x = run_mr(d, s, thr=5, exit_rule="rsi70", k_stop=2.0)  # real signals, for frequency reference
        # emulate random entries by shifting real entry dates randomly within uptrend days
        up = d.index[(d.close > d.close.rolling(200).mean())]
        if len(x) == 0 or len(up) == 0:
            continue
        pick = np.sort(rng.choice(up[200:], size=len(x), replace=False)) if len(up) > 200 + len(x) else []
        for ts in pick:
            i = d.index.get_loc(ts)
            sub = d.iloc[: i + 15]
            y = run_mr(sub.assign(close=sub.close), s, thr=101, exit_rule="rsi70", k_stop=2.0)  # thr=101 -> enter every uptrend day
            y = y[y.entry_ts == int(d.index[i + 1])] if i + 1 < len(d) else y.iloc[0:0]
            ctrl.append(y)
        break
ctrl = pd.concat(ctrl) if ctrl else pd.DataFrame(columns=["r"])
ctrl = ctrl[(ctrl.entry_ts >= TESTS[0][0]) & ctrl.direction.isin(dirs)]
print("control (random uptrend-day long entries, same exits, 2015-2026):", brief(ctrl["r"]))
