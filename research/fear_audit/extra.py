"""Fear audit, part 2: robustness diagnostics on out_trades_all.csv (written by audit.py).
Everything here is diagnostic; the pre-registered verdict inputs are in audit.py's output.
    cd research/fear_audit && python extra.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit as A  # noqa: E402

os.chdir(HERE)
rng = np.random.default_rng(7)
pd.set_option("display.width", 250)
lines = []
def P(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); lines.append(s)

T = pd.read_csv("out_trades_all.csv", parse_dates=["entry_date", "exit_date", "sig_date"])
days = pd.bdate_range(T.exit_date.min(), T.exit_date.max())
idx = days.searchsorted(T.exit_date); w = T.r.to_numpy() * A.RISK
s_on = A.sharpe(np.bincount(idx, weights=w * (~T.veto).to_numpy(), minlength=len(days)))

# 1. date-clustered random veto: veto ALL candidates on randomly chosen signal dates until the same count is reached
k = int(T.veto.sum())
by_date = T.groupby("sig_date").indices
dates = np.array(list(by_date.keys()))
sims = []
for _ in range(1000):
    keep = np.ones(len(T)); n = 0
    for d in rng.permutation(dates):
        ii = by_date[d]
        if n + len(ii) > k * 1.02:
            continue
        keep[ii] = 0; n += len(ii)
        if n >= k * 0.98:
            break
    sims.append(A.sharpe(np.bincount(idx, weights=w * keep, minlength=len(days))))
sims = np.array(sims)
P(f"[1] date-clustered random veto ({k} trades): mean Sharpe {sims.mean():.3f}, 95th pct {np.percentile(sims, 95):.3f}, "
  f"filter {s_on:.3f}, p = {(sims >= s_on).mean():.3f}")

# 2. year by year for the equity core and for the markets the live engine trades now
LIVE = ["iwmususd", "dvyususd", "efaususd", "xlfususd", "xliususd", "xlkususd", "xleususd", "xlvususd", "iyrususd",
        "smhususd", "arkqususd", "jetsususd"]
for lab, x0 in [("PRIMARY+REF", T[T.group.isin(["PRIMARY", "REF"])]), ("live-engine ETFs (MetaQuotes demo)", T[T.sym.isin(LIVE)])]:
    rows = []
    for y, x in x0.groupby(x0.exit_date.dt.year):
        dd = pd.bdate_range(f"{y}-01-01", f"{y}-12-31")
        a, b = A.daily_pnl(x, dd), A.daily_pnl(x, dd, (~x.veto).to_numpy())
        rows.append(dict(year=y, n=len(x), vetoed=int(x.veto.sum()), exp_off=round(x.r.mean(), 3),
                         exp_on=round(x.r[~x.veto].mean(), 3), sh_off=round(A.sharpe(a), 2), sh_on=round(A.sharpe(b), 2)))
    Y = pd.DataFrame(rows)
    dd = pd.bdate_range(x0.exit_date.min(), x0.exit_date.max())
    P(f"\n[2] {lab}: Sharpe off {A.sharpe(A.daily_pnl(x0, dd)):.3f} on {A.sharpe(A.daily_pnl(x0, dd, (~x0.veto).to_numpy())):.3f} | "
      f"helps {(Y.sh_on > Y.sh_off).sum()} / hurts {(Y.sh_on < Y.sh_off).sum()} years")
    P(Y.to_string(index=False))

# 3. anatomy of the top 5% of trades
q = T.r.quantile(0.95)
top = T[T.r >= q]
P(f"\n[3] top 5% trades: n {len(top)}, R >= {q:.2f}, mean {top.r.mean():+.2f}R, max {top.r.max():+.2f}R; "
  f"share of total R of all trades: {top.r.sum() / T.r.sum():.0%}")
P("    by year:", top.year.value_counts().sort_index().to_dict())
P("    by class:", top.cls.value_counts().to_dict())
P(f"    vetoed by the filter: {top.veto.mean():.0%} (all trades: {T.veto.mean():.0%}) | held {top.days.mean():.1f} days avg")
P("    top 5% within US equity index/ETF purge survives:", round(T[T.cls == 'US equity index/ETF'].r.sort_values().iloc[:-int(0.05 * (T.cls == 'US equity index/ETF').sum())].mean(), 4))

# 4. crisis windows, PRIMARY+REF only (the pre-registered equity core)
WIN = [("Aug 2011", "2011-07-25", "2011-10-31"), ("China deval 2015", "2015-08-10", "2015-09-30"),
       ("Volmageddon 2018", "2018-01-26", "2018-04-30"), ("Q4 2018", "2018-10-01", "2018-12-31"),
       ("COVID 2020", "2020-02-19", "2020-04-30"), ("Bear 2022", "2022-01-03", "2022-10-31"),
       ("Yen carry 2024", "2024-07-15", "2024-08-31"), ("Tariffs 2025", "2025-02-19", "2025-04-30")]
core = T[T.group.isin(["PRIMARY", "REF"])]
rows = []
for name, a, b in WIN:
    x = core[(core.entry_date >= a) & (core.entry_date <= b)].sort_values("exit_date")
    if not len(x):
        continue
    def path(xx):
        if not len(xx):
            return 0.0, 0.0, np.nan
        cum = xx.r.cumsum().to_numpy(); pk = np.maximum.accumulate(np.r_[0, cum])[1:]
        return round(cum[-1], 2), round((pk - cum).max(), 2), (xx.exit_date.iloc[int(np.argmin(cum))] - pd.Timestamp(a)).days
    c0, d0, t0 = path(x); c1, d1, t1 = path(x[~x.veto])
    so = x[x.stopout]
    rows.append(dict(window=name, n=len(x), cumR_off=c0, ddR_off=d0, days_to_trough=t0, perTrade_off=round(x.r.mean(), 3),
                     n_on=int((~x.veto).sum()), cumR_on=c1, ddR_on=d1, stopouts=len(so),
                     avg_stop_R=round(so.r.mean(), 3) if len(so) else np.nan, worst=round(x.r.min(), 2)))
P("\n[4] crisis windows, PRIMARY+REF only\n" + pd.DataFrame(rows).to_string(index=False))

# 5. the same filter on every other strategy's saved trade log (fear state as of the previous day's close; F5 n/a)
D = pd.DatetimeIndex
eq = []
for s, path, g, cost in A.universe():
    if A.asset_class(s) in ("US equity index/ETF", "Non-US equity index/ETF"):
        x = A.load_any(s, path)
        eq.append(pd.Series(np.where(np.isnan(x["sma50"]), np.nan, (x["c"] < x["sma50"]).astype(float)),
                            index=pd.to_datetime(x["ts"], unit="ms").normalize()))
breadth = pd.concat(eq, axis=1, sort=True).mean(axis=1, skipna=True)
F = A.fear_series(breadth)
br = F["f4"]
def flags(dates: pd.DatetimeIndex) -> np.ndarray:
    f1 = A.asof(F["f1"].astype(float), dates, lag=True)
    f2 = A.asof(F["f2"].astype(float), dates, lag=True)
    f3 = A.asof(F["f3"].astype(float), dates, lag=True)
    f4 = A.asof(br.astype(float), dates, lag=True)
    return np.nan_to_num(f1) + np.nan_to_num(f2) + np.nan_to_num(f3) + np.nan_to_num(f4)
logs = [("VWAP A pullback", "out_vwap_trades_A.csv", "date", None), ("VWAP B reclaim", "out_vwap_trades_B.csv", "date", None),
        ("VWAP C band fade", "out_vwap_trades_C.csv", "date", None), ("hourly dip-buy H-A", "out_hourly_H-A.csv", "entry_ts", "ms"),
        ("hourly dip-buy H-B", "out_hourly_H-B.csv", "entry_ts", "ms"), ("ATR-EW walk-forward A", "out_wf_oos_trades_A.csv", "entry_ts", "ms"),
        ("ATR-EW walk-forward B", "out_wf_oos_trades_B.csv", "entry_ts", "ms"),
        ("daily MR dev (idx+metals) A", "out_mr_oos_idx_metals_long_A.csv", "entry_ts", "ms")]
rows = []
for name, f, col, unit in logs:
    t = pd.read_csv(f"../{f}")
    dts = (pd.to_datetime(t[col], unit=unit) if unit else pd.to_datetime(t[col])).dt.normalize()
    sc = flags(D(dts))
    veto = sc >= 2
    for thr, vv in [("≥2 of F1-F4", veto)]:
        rows.append(dict(strategy=name, n=len(t), exp_off=round(t.r.mean(), 4), n_on=int((~vv).sum()),
                         exp_on=round(t.r[~vv].mean(), 4), vetoed=int(vv.sum()),
                         exp_vetoed=round(t.r[vv].mean(), 4) if vv.any() else np.nan))
P("\n[5] filter (F1-F4, >=2, as of the previous close) on every other strategy's trades\n" + pd.DataFrame(rows).to_string(index=False))

# 6. post-hoc diagnostic only (NOT selectable): composite without the candle proxy F5
for thr in (1, 2):
    sc = sum(np.nan_to_num(T[c].astype(float)) for c in ["f1", "f2", "f3", "f4"])
    vv = sc >= thr
    s = A.sharpe(np.bincount(idx, weights=w * (~vv), minlength=len(days)))
    P(f"[6] post-hoc: veto if >= {thr} of F1-F4 (no candle proxy): vetoes {vv.mean():.0%}, exp on {T.r[~vv].mean():+.4f}, "
      f"Sharpe {s:.3f}  (diagnostic only)")

with open("out_extra_log.txt", "w") as f:
    f.write("\n".join(lines))
