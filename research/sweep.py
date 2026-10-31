"""Small robustness sweep: choose settings on 2007-2018 (in-sample), then check 2019-2026 (out-of-sample).
Prints the top in-sample settings and how they did out-of-sample."""
import itertools, sys
import pandas as pd
from dataclasses import replace
from atr_ew_backtest import Params, run, load, stats

df = load(sys.argv[1])
split = pd.Timestamp("2019-01-01").value // 1_000_000
grid = dict(trig_frac=[0.5, 1.0], min_swing_atr=[1.0, 1.5, 2.5], sl_mode=["tighter", "structural"],
            r_floor=[(1.0, 2.0, 3.0), (1.5, 3.0, 5.0)], w3_trend_req=[False, True], min_adx=[18, 25])
rows = []
for combo in itertools.product(*grid.values()):
    p = replace(Params(), **dict(zip(grid, combo)))
    tr = run(df, p)
    if tr.empty: continue
    ins, oos = tr[tr.entry_ts < split]["r"], tr[tr.entry_ts >= split]["r"]
    si, so = stats(ins), stats(oos)
    rows.append(dict(zip(grid, combo)) | {"IS_n": si.get("n"), "IS_avgR": si.get("avgR"), "IS_PF": si.get("PF"),
                 "OOS_n": so.get("n"), "OOS_avgR": so.get("avgR"), "OOS_PF": so.get("PF"), "OOS_totalR": so.get("totalR")})
res = pd.DataFrame(rows).sort_values("IS_avgR", ascending=False)
pd.set_option("display.width", 250)
print(f"{len(res)} settings tested. Positive in-sample: {(res.IS_avgR > 0).sum()}, positive OOS: {(res.OOS_avgR > 0).sum()}, both: {((res.IS_avgR > 0) & (res.OOS_avgR > 0)).sum()}")
print("Top 10 by in-sample avgR:")
print(res.head(10).to_string(index=False))
print("Rank correlation IS vs OOS avgR:", round(res.IS_avgR.corr(res.OOS_avgR, method="spearman"), 2))
