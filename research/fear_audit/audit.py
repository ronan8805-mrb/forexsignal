"""Fear-filter audit (read-only; rules in FEAR_PREREG.md).

Runs the UNCHANGED daily dip-buy (research/validate_mr.simulate) on every market in the dataset, attaches
a five-component fear vote to each candidate trade, and compares filter off vs on.
    cd research/fear_audit && python audit.py        (writes out_*.csv / out_*.txt here, never outside)
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.dirname(HERE)
sys.path.insert(0, RES)
import validate_mr as v  # noqa: E402  (read-only use of the pre-registered code)

RISK = 0.005
REPS = 1000
rng = np.random.default_rng(31102026)

# ------------------------------------------------------------------------------------------------ universe
def c(sp, sl, sw):
    return dict(spread=0.0, slip=0.0, spread_bps=sp, slip_bps=sl, swap_bps=sw)

STOCK, FX, FXW, CMD, CRY, OTH = c(6, 1.5, 2), c(1.0, 0.3, 0.5), c(3, 1, 0.5), c(3, 1, 2), c(10, 3, 5), c(3, 1, 2)
US_ETF_EQ = {"spyususd", "qqqususd", "diaususd", "iwmususd", "dvyususd", "xlfususd", "xliususd", "xlkususd",
             "xleususd", "xlvususd", "iyrususd", "smhususd", "arkqususd", "jetsususd"}
CLASS = {**{s: "US equity index/ETF" for s in US_ETF_EQ | {"usa500idxusd", "usatechidxusd", "usa30idxusd"}},
         "efaususd": "Non-US equity index/ETF", "eemususd": "Non-US equity index/ETF",
         **{s: "Non-US equity index/ETF" for s in ("deuidxeur", "gbridxgbp", "jpnidxjpy", "hkgidxhkd", "chiidxusd")},
         **{s: "Metals" for s in ("xauusd", "xagusd", "gldususd", "slvususd", "gdxususd", "xpdcmdusd", "xptcmdusd", "iglnususd")},
         **{s: "Bonds/credit" for s in ("tltususd", "jnkususd", "bundtreur", "ustbondtrusd", "ukgilttrgbp")},
         **{s: "Energy/ags" for s in ("usoususd", "brentcmdusd", "lightcmdusd", "gascmdusd", "dieselcmdusd", "coppercmdusd",
                                       "cocoacmdusd", "coffeecmdusx", "cottoncmdusx", "ojuicecmdusx", "soybeancmdusx", "sugarcmdusd")},
         **{s: "FX" for s in ("eurusd", "gbpusd", "usdjpy", "audusd", "nzdusd", "usdcad", "usdchf", "usdcnh", "usdsek", "dollaridxusd")},
         **{s: "Crypto" for s in ("btcusd", "ethusd", "bitoususd")}, "vxxususd": "Volatility ETN"}


def universe() -> list[tuple[str, str, str, dict]]:
    """(symbol, path, pre-registered group, cost) for all 152 markets."""
    out = []
    for g, members in v.GROUPS.items():
        for s, cost in members.items():
            out.append((s, f"{RES}/data_daily/{s}.csv", g, cost))
    for f in sorted(os.listdir(f"{RES}/data_daily_stocks")):
        s = f[:-4]
        out.append((s, f"{RES}/data_daily_stocks/{f}", "STOCKS", STOCK))
    for f in sorted(os.listdir(f"{HERE}/data_daily_extra")):
        s = f[:-4]
        cl = CLASS.get(s, "Single stocks")
        cost = {"FX": FXW if s in ("usdcnh", "usdsek") else FX, "Crypto": CRY, "Energy/ags": CMD, "Metals": CMD,
                "Single stocks": STOCK}.get(cl, OTH)
        grp = {"FX": "FX", "Crypto": "CRYPTO", "Energy/ags": "COMMOD", "Metals": "COMMOD", "Single stocks": "STOCKS"}.get(cl, "OTHER")
        out.append((s, f"{HERE}/data_daily_extra/{f}", grp, cost))
    return out


def asset_class(s: str) -> str:
    if s in CLASS:
        return CLASS[s]
    if s.endswith("jpjpy"):
        return "Single stocks JP"
    if s.endswith("ususd"):
        return "Single stocks US"
    return "Single stocks EU/UK/CH"


def load_any(sym: str, path: str) -> dict:
    """Identical maths to validate_mr.load_daily, but from any folder."""
    d = v.split_adjust(sym, pd.read_csv(path, index_col="timestamp"))
    cl = d["close"]
    delta = cl.diff()
    up = delta.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)
    pc = cl.shift(1)
    tr = np.maximum(d.high - d.low, np.maximum((d.high - pc).abs(), (d.low - pc).abs()))
    return dict(ts=d.index.to_numpy(), o=d.open.to_numpy(), h=d.high.to_numpy(), l=d.low.to_numpy(),
                c=cl.to_numpy(), rsi=rsi.to_numpy(), sma=cl.rolling(200).mean().to_numpy(),
                atr=tr.ewm(alpha=1 / 14, adjust=False).mean().to_numpy(), sma50=cl.rolling(50).mean().to_numpy())


# ------------------------------------------------------------------------------------------------ fear inputs
def cboe(path: str) -> pd.Series:
    d = pd.read_csv(path)
    d.index = pd.to_datetime(d.DATE, format="%m/%d/%Y")
    return d.CLOSE.astype(float)


def fear_series(breadth: pd.Series) -> pd.DataFrame:
    """Daily fear states indexed by calendar date (states are 'as of that date's US close')."""
    D = f"{HERE}/data"
    vix, v3m = cboe(f"{D}/VIX_History.csv"), cboe(f"{D}/VIX3M_History.csv")
    f1_real = (vix / v3m).dropna() >= 1.0
    f1_proxy = vix >= 1.3 * vix.rolling(60).mean()
    f1 = f1_proxy.copy()
    f1.loc[f1_real.index] = f1_real
    f1_src = pd.Series("proxy", index=f1.index); f1_src.loc[f1_real.index] = "VIX/VIX3M"
    # put/call on low volume (equity options), lagged one day later in asof()
    pcr = pd.read_csv(f"{D}/equitypc.csv", skiprows=2)
    pcr.columns = [x.strip() for x in pcr.columns]
    pcr.index = pd.to_datetime(pcr.DATE.astype(str).str.strip(), format="%m/%d/%Y")
    ratio, vol = pcr["P/C Ratio"].astype(float), pcr["TOTAL"].astype(float)
    r10, v10 = ratio.rolling(10).mean(), vol.rolling(10).mean()
    pct = r10.rolling(252).apply(lambda w: (w <= w[-1]).mean(), raw=True)
    f2 = ((pct >= 0.8) & (v10 < vol.rolling(252).median())).where(pct.notna())
    dix = pd.read_csv(f"{D}/DIX.csv", parse_dates=["date"]).set_index("date")
    f3 = dix.gex < 0
    f4 = breadth >= 0.60
    return dict(vix=vix, f1=f1, f1_src=f1_src, f2=f2, f3=f3, f4=f4)


def asof(s: pd.Series, dates: pd.DatetimeIndex, lag: bool) -> np.ndarray:
    """Value at the latest date <= D (lag=False) or < D (lag=True). NaN if none within 7 days."""
    s = s.dropna().sort_index()
    idx = s.index.searchsorted(dates, side="left" if lag else "right") - 1
    out = np.full(len(dates), np.nan)
    ok = idx >= 0
    vals = s.to_numpy(dtype=float)
    out[ok] = vals[idx[ok]]
    stale = np.zeros(len(dates), bool)
    stale[ok] = (dates[ok] - s.index[idx[ok]]).days > 7
    out[stale] = np.nan
    return out


# ------------------------------------------------------------------------------------------------ build trades
def build() -> pd.DataFrame:
    rows, eq_breadth = [], []
    data = {}
    for s, path, g, cost in universe():
        x = load_any(s, path)
        data[s] = (x, g, cost)
        if asset_class(s) in ("US equity index/ETF", "Non-US equity index/ETF"):
            below = pd.Series(np.where(np.isnan(x["sma50"]), np.nan, (x["c"] < x["sma50"]).astype(float)),
                              index=pd.to_datetime(x["ts"], unit="ms").normalize())
            eq_breadth.append(below)
    breadth = pd.concat(eq_breadth, axis=1).mean(axis=1, skipna=True)
    F = fear_series(breadth)
    for s, (x, g, cost) in data.items():
        up = (x["c"] > x["sma"]) & ~np.isnan(x["sma"])
        tr = v.simulate(x, up & (x["rsi"] < v.THR), cost)
        if not tr:
            continue
        t = pd.DataFrame(tr, columns=["entry_ts", "exit_ts", "r", "r_gross", "days"])
        ei = np.searchsorted(x["ts"], t.entry_ts.to_numpy())
        si = ei - 1                                      # signal bar
        rngd = x["h"][si] - x["l"][si]
        t["f5"] = np.where(rngd > 0, (x["c"][si] - x["l"][si]) / np.where(rngd > 0, rngd, 1) < 0.25, False)
        t["sym"], t["group"], t["cls"] = s, g, asset_class(s)
        t["sig_date"] = pd.to_datetime(x["ts"][si], unit="ms").normalize()
        rows.append(t)
    T = pd.concat(rows, ignore_index=True)
    d = pd.DatetimeIndex(T.sig_date)
    T["f1"] = asof(F["f1"].astype(float), d, lag=False)
    T["f2"] = asof(F["f2"].astype(float), d, lag=True)
    T["f3"] = asof(F["f3"].astype(float), d, lag=True)
    T["f4"] = asof(F["f4"].astype(float), d, lag=False)
    T["vix"] = asof(F["vix"], d, lag=False)
    src = F["f1_src"].map({"VIX/VIX3M": 1.0, "proxy": 0.0}).astype(float)
    T["f1_real"] = asof(src, d, lag=False)
    comps = ["f1", "f2", "f3", "f4", "f5"]
    T["score"] = sum(np.nan_to_num(T[k].astype(float), nan=0.0) for k in comps)
    T["veto"] = T.score >= 2
    T["entry_date"] = pd.to_datetime(T.entry_ts, unit="ms").dt.normalize()
    T["exit_date"] = pd.to_datetime(T.exit_ts, unit="ms").dt.normalize()
    T["year"] = T.entry_date.dt.year
    T["stopout"] = T.r_gross <= -0.98
    return T, F


# ------------------------------------------------------------------------------------------------ metrics
def m(r: pd.Series) -> dict:
    r = pd.Series(r, dtype=float)
    if len(r) == 0:
        return dict(n=0, win=np.nan, exp=np.nan, PF=np.nan)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), win=round(100 * (r > 0).mean(), 1), exp=round(r.mean(), 4),
                PF=round(pos / neg, 2) if neg > 0 else np.nan)


def onoff(t: pd.DataFrame) -> dict:
    a, b = m(t.r), m(t.r[~t.veto])
    return dict(n_off=a["n"], exp_off=a["exp"], PF_off=a["PF"], win_off=a["win"],
                n_on=b["n"], exp_on=b["exp"], PF_on=b["PF"], win_on=b["win"],
                vetoed=int(t.veto.sum()), exp_vetoed=round(t.r[t.veto].mean(), 4) if t.veto.any() else np.nan)


def daily_pnl(t: pd.DataFrame, days: pd.DatetimeIndex, keep=None) -> np.ndarray:
    idx = days.searchsorted(t.exit_date)
    w = t.r.to_numpy() * RISK * (1.0 if keep is None else keep)
    return np.bincount(idx, weights=w, minlength=len(days))


def sharpe(p: np.ndarray) -> float:
    return float(np.sqrt(252) * p.mean() / p.std()) if p.std() > 0 else np.nan


# ------------------------------------------------------------------------------------------------ main
if __name__ == "__main__":
    os.chdir(HERE)
    t0 = time.time()
    T, F = build()
    T.to_csv("out_trades_all.csv", index=False)
    lines = []
    def P(*a):
        s = " ".join(str(x) for x in a); print(s, flush=True); lines.append(s)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 400)

    P(f"built {len(T)} baseline trades on {T.sym.nunique()} markets in {time.time() - t0:.0f}s")
    # sanity: PRIMARY via load_any == reproduced baseline
    prim = T[T.group == "PRIMARY"]
    P(f"identity check PRIMARY: n {len(prim)} exp {prim.r.mean():+.4f} (reproduced baseline: 1307, +0.043)")

    # coverage of each component
    cov = {k: f"{T[k].notna().mean():.0%} have data, {np.nanmean(T[k].astype(float)):.1%} of those in fear" for k in ["f1", "f2", "f3", "f4", "f5"]}
    P("component coverage:", cov, f"| F1 real VIX3M share {np.nanmean(T.f1_real):.0%}")
    P(f"composite: {T.veto.mean():.1%} of all candidate trades vetoed")

    # 1. on/off by pre-registered group, asset class, overall
    P("\n=== FILTER OFF vs ON, by pre-registered group (net R) ===")
    P(pd.DataFrame({g: onoff(x) for g, x in T.groupby("group")}).T.to_string())
    P("\n=== by asset class ===")
    P(pd.DataFrame({g: onoff(x) for g, x in T.groupby("cls")}).T.to_string())
    P("\n=== ALL MARKETS ===", onoff(T))
    hold = T.entry_date >= "2023-01-01"
    P("PRIMARY 2013-22:", onoff(prim[~hold[prim.index]]), "\nPRIMARY 2023-26 holdout:", onoff(prim[hold[prim.index]]))
    P("ALL 2023-26 holdout:", onoff(T[hold]))

    # per instrument
    per = pd.DataFrame({s: {"cls": x.cls.iloc[0], **onoff(x)} for s, x in T.groupby("sym")}).T
    per.to_csv("out_per_instrument.csv")
    better = (per.exp_on.astype(float) > per.exp_off.astype(float)).sum()
    P(f"\nper instrument: filter raises expectancy in {better} of {len(per)} markets "
      f"({(per.vetoed.astype(int) == 0).sum()} markets had nothing vetoed)")

    # regimes
    T["vix_reg"] = pd.cut(T.vix, [0, 15, 25, 1e9], labels=["VIX<15", "VIX15-25", "VIX>25"])
    P("\n=== by VIX regime at signal (all markets) ===")
    P(pd.DataFrame({str(g): onoff(x) for g, x in T.groupby("vix_reg", observed=True)}).T.to_string())
    P("\n=== components alone (diagnostic, never selected): expectancy of trades where the component is in fear vs not ===")
    for k in ["f1", "f2", "f3", "f4", "f5"]:
        x = T[T[k].notna()]
        P(f"  {k}: fear n={int((x[k] == 1).sum())} exp {x.r[x[k] == 1].mean():+.4f} | calm n={int((x[k] == 0).sum())} exp {x.r[x[k] == 0].mean():+.4f}")
    P("  inverse (trade ONLY when composite vetoes):", m(T.r[T.veto]))

    # 2. the single number: portfolio Sharpe, random veto
    days = pd.bdate_range(T.exit_date.min(), T.exit_date.max())
    p_off, p_on = daily_pnl(T, days), daily_pnl(T, days, keep=(~T.veto).to_numpy())
    s_off, s_on = sharpe(p_off), sharpe(p_on)
    k = int(T.veto.sum())
    idx = days.searchsorted(T.exit_date); w = T.r.to_numpy() * RISK
    sims = np.empty(REPS)
    for i in range(REPS):
        keep = np.ones(len(T)); keep[rng.choice(len(T), k, replace=False)] = 0
        sims[i] = sharpe(np.bincount(idx, weights=w * keep, minlength=len(days)))
    pval = float((sims >= s_on).mean())
    P(f"\n=== THE NUMBER: portfolio Sharpe (all {T.sym.nunique()} markets, 0.5% risk/trade, after costs) ===")
    P(f"  filter OFF {s_off:.3f} | filter ON {s_on:.3f} | change {s_on - s_off:+.3f} | random veto of the same {k} trades: "
      f"mean {sims.mean():.3f}, 95th pct {np.percentile(sims, 95):.3f}, p = {pval:.3f}")
    for lab, msk in [("PRIMARY", T.group == "PRIMARY"), ("PRIMARY+REF", T.group.isin(["PRIMARY", "REF"]))]:
        tt = T[msk]
        dd = pd.bdate_range(tt.exit_date.min(), tt.exit_date.max())
        P(f"  {lab}: Sharpe off {sharpe(daily_pnl(tt, dd)):.3f} on {sharpe(daily_pnl(tt, dd, (~tt.veto).to_numpy())):.3f}")
    # by year
    yr = []
    for y, x in T.groupby(T.exit_date.dt.year):
        dd = pd.bdate_range(f"{y}-01-01", f"{y}-12-31")
        a, b = daily_pnl(x, dd), daily_pnl(x, dd, (~x.veto).to_numpy())
        yr.append(dict(year=y, n=len(x), vetoed=int(x.veto.sum()), exp_off=round(x.r.mean(), 4),
                       exp_on=round(x.r[~x.veto].mean(), 4), sharpe_off=round(sharpe(a), 2), sharpe_on=round(sharpe(b), 2),
                       ret_off_pct=round(100 * a.sum(), 1), ret_on_pct=round(100 * b.sum(), 1)))
    Y = pd.DataFrame(yr); Y["helps"] = Y.sharpe_on > Y.sharpe_off
    P("\n=== year by year (all markets) ===\n" + Y.to_string(index=False))
    P(f"  filter improves Sharpe in {int(Y.helps.sum())} of {len(Y)} years; "
      f"2023-26 share of total Sharpe-points gained: {(Y[Y.year >= 2023].sharpe_on - Y[Y.year >= 2023].sharpe_off).sum():+.2f} "
      f"of {(Y.sharpe_on - Y.sharpe_off).sum():+.2f}")
    Y.to_csv("out_by_year.csv", index=False)

    # 3. stress 1: winner purge
    P("\n=== STRESS 1: winner purge (drop top 5% of trades by R) ===")
    def purge(r):
        r = pd.Series(r).sort_values()
        return r.iloc[: int(np.floor(len(r) * 0.95))].mean() if len(r) >= 20 else np.nan
    wp = []
    for lab, x in [("ALL", T), *[(g, xx) for g, xx in T.groupby("group")], *[(g, xx) for g, xx in T.groupby("cls")]]:
        wp.append(dict(set=lab, n=len(x), exp_off=round(x.r.mean(), 4), purged_off=round(purge(x.r), 4),
                       exp_on=round(x.r[~x.veto].mean(), 4), purged_on=round(purge(x.r[~x.veto]), 4)))
    P(pd.DataFrame(wp).to_string(index=False))

    # stress 2: crisis windows
    P("\n=== STRESS 2: crisis windows (trades entered in the window; all markets with data) ===")
    WIN = [("GFC 2008", "2008-09-01", "2009-03-31"), ("Aug 2011", "2011-07-25", "2011-10-31"),
           ("China deval 2015", "2015-08-10", "2015-09-30"), ("Volmageddon 2018", "2018-01-26", "2018-04-30"),
           ("Q4 2018", "2018-10-01", "2018-12-31"), ("COVID 2020", "2020-02-19", "2020-04-30"),
           ("Bear 2022", "2022-01-03", "2022-10-31"), ("Yen carry 2024", "2024-07-15", "2024-08-31"),
           ("Tariffs 2025", "2025-02-19", "2025-04-30")]
    # data-defined breakdowns: rolling 30-trade expectancy of PRIMARY+REF below -0.30R
    pr = T[T.group.isin(["PRIMARY", "REF"])].sort_values("exit_date")
    roll = pr.r.rolling(30).mean().to_numpy()
    bad = roll < -0.30
    ep, start = [], None
    ex = pr.exit_date.to_numpy()
    for i in range(len(bad)):
        if bad[i] and start is None:
            start = i
        if (not bad[i] or i == len(bad) - 1) and start is not None:
            end = i if bad[i] else i - 1
            ep.append((f"breakdown {pd.Timestamp(ex[max(start - 29, 0)]).date()}", str(pd.Timestamp(ex[max(start - 29, 0)]).date()),
                       str(pd.Timestamp(ex[end]).date())))
            start = None
    P(f"  data-defined breakdown episodes (rolling-30 PRIMARY+REF < -0.30R): {len(ep)}")
    cw = []
    for name, a, b in WIN + ep:
        x = T[(T.entry_date >= a) & (T.entry_date <= b)].sort_values("exit_date")
        if len(x) == 0:
            cw.append(dict(window=name, n=0)); continue
        res = {}
        for lab, xx in [("off", x), ("on", x[~x.veto])]:
            if len(xx) == 0:
                res.update({f"cumR_{lab}": 0.0}); continue
            cum = xx.r.cumsum().to_numpy()
            peak = np.maximum.accumulate(np.r_[0, cum])[1:]
            ddR = (peak - cum).max()
            trough = xx.exit_date.iloc[int(np.argmin(cum))]
            res.update({f"n_{lab}": len(xx), f"cumR_{lab}": round(cum[-1], 2), f"maxDD_R_{lab}": round(ddR, 2),
                        f"days_to_trough_{lab}": (trough - pd.Timestamp(a)).days})
        so = x[x.stopout]
        cw.append(dict(window=name, markets=x.sym.nunique(), **res, stopouts=len(so),
                       avg_stop_R=round(so.r.mean(), 3) if len(so) else np.nan,
                       worst_R=round(x.r.min(), 2), gaps_beyond_1_2R=int((x.r < -1.2).sum()),
                       classes=", ".join(sorted(x.cls.unique()))[:60]))
    CW = pd.DataFrame(cw)
    P(CW.drop(columns=["classes"]).to_string(index=False))
    CW.to_csv("out_crisis_windows.csv", index=False)
    P("  2008 coverage note: markets with trades in the GFC window ->",
      ", ".join(sorted(T[(T.entry_date >= "2008-09-01") & (T.entry_date <= "2009-03-31")].sym.unique())))

    # stress 3: cross-instrument
    P("\n=== STRESS 3: cross-instrument (net R, filter off / on, share of markets positive) ===")
    ci = []
    for g, x in T.groupby("cls"):
        ps = x.groupby("sym").r.mean()
        pon = x[~x.veto].groupby("sym").r.mean()
        ci.append(dict(cls=g, markets=x.sym.nunique(), n=len(x), exp_off=round(x.r.mean(), 4),
                       exp_on=round(x.r[~x.veto].mean(), 4), pos_off=f"{(ps > 0).sum()}/{len(ps)}",
                       pos_on=f"{(pon > 0).sum()}/{len(pon)}", gross_off=round(x.r_gross.mean(), 4)))
    P(pd.DataFrame(ci).to_string(index=False))

    with open("out_audit_log.txt", "w") as f:
        f.write("\n".join(lines))
    P(f"\ndone in {time.time() - t0:.0f}s")
