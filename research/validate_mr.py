"""Pre-registered out-of-instrument validation of the RSI(2) dip-buy alternative.

FIXED settings (declared in REPORT.md section 8 before this test): long only,
close > SMA200 and RSI(2) < 10 -> buy next open; exit on daily close RSI(2) > 70,
3 x ATR(14) stop, 10-day time stop. Nothing is tuned here.

Groups (fixed before looking at results):
  PRIMARY  equity indices + equity ETFs never used before
  SECOND   other asset classes (contrast only: metals ETFs, bonds, oil, high yield)
  REF      the 5 instruments the rule was developed on (in-sample reference)
Excluded: VXX, BITO (roll-decay futures products).

Controls use ONE random draw per calendar day shared by all instruments, so random entries
cluster across correlated ETFs exactly like real dip signals do.
Controls (does the dip TIMING add anything beyond being long in an uptrend?):
  C1 random entries on uptrend days (close > SMA200), same density, same exit rules
  C2 random entries on uptrend days, holding period drawn from the real trades' holding
     periods, same 3-ATR stop  (isolates entry timing from the exit rule)
1,000 repetitions each; p-value = share of control runs whose pooled expectancy >= real.
Significance also via t-stat on weekly-clustered returns (trades on the same week across
correlated instruments are summed first, so correlated trades don't inflate N).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from robustness import brief, full_metrics

THR, EXIT_RSI, K_STOP, MAX_DAYS = 10, 70, 3.0, 10
REPS = 1000
rng = np.random.default_rng(2026)

IDX = dict(spread=0.0, slip=0.0, spread_bps=2.0, slip_bps=0.5, swap_bps=2.0)
ETF = dict(spread=0.0, slip=0.0, spread_bps=3.0, slip_bps=1.0, swap_bps=2.0)
BOND = dict(spread=0.0, slip=0.0, spread_bps=2.0, slip_bps=0.5, swap_bps=1.5)
GROUPS = {
    "PRIMARY": {**{s: IDX for s in ("usa30idxusd", "jpnidxjpy", "gbridxgbp", "hkgidxhkd", "chiidxusd")},
                **{s: ETF for s in ("spyususd", "qqqususd", "diaususd", "iwmususd", "dvyususd", "eemususd",
                                    "efaususd", "xlfususd", "xliususd", "xlkususd", "xleususd", "xlvususd",
                                    "iyrususd", "smhususd", "arkqususd", "jetsususd")}},
    "SECOND": {**{s: ETF for s in ("gldususd", "slvususd", "gdxususd", "tltususd", "jnkususd", "usoususd")},
               **{s: BOND for s in ("bundtreur", "ustbondtrusd", "ukgilttrgbp")}},
    "REF": {"usa500idxusd": IDX, "usatechidxusd": IDX, "deuidxeur": IDX,
            "xauusd": dict(spread=0.30, slip=0.05, spread_bps=0, slip_bps=0, swap_bps=1.5),
            "xagusd": dict(spread=0.025, slip=0.005, spread_bps=0, slip_bps=0, swap_bps=1.5)},
}


SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 8, 1 / 10)
SPLIT_LOG = []


def split_adjust(sym: str, d: pd.DataFrame) -> pd.DataFrame:
    """Dukascopy ETF CFD history is NOT split-adjusted. Detect overnight gaps whose ratio is
    within 3% of a standard split ratio (and > 40% in size) and back-adjust earlier prices."""
    d = d.copy()
    ratio = (d.open / d.close.shift(1)).to_numpy()
    for i in range(1, len(d)):
        q = ratio[i]
        if abs(np.log(q)) < np.log(1.4):
            continue
        for sr in SPLIT_RATIOS:
            if abs(q / sr - 1) < 0.03:
                d.iloc[:i, d.columns.get_indexer(["open", "high", "low", "close"])] *= sr
                SPLIT_LOG.append((sym, str(pd.to_datetime(d.index[i], unit="ms").date()), round(1 / sr, 3)))
                break
    return d


def load_daily(sym: str) -> dict:
    d = split_adjust(sym, pd.read_csv(f"data_daily/{sym}.csv", index_col="timestamp"))
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


def simulate(x: dict, mask: np.ndarray, cost: dict, hold: np.ndarray | None = None) -> list[tuple]:
    """Long-only. mask[i] True = signal at close of day i -> buy open i+1.
    hold=None -> exit on RSI>70 / 10-day time stop; hold=array -> exit after hold[k] days (C2).
    Returns tuples (entry_ts, exit_ts, r, r_gross, days)."""
    o, h, l, c, rsi, atr, ts = x["o"], x["h"], x["l"], x["c"], x["rsi"], x["atr"], x["ts"]
    n, out, i, k = len(o), [], 201, 0
    while i < n - 1:
        if not mask[i]:
            i += 1
            continue
        e = i + 1
        sp = cost["spread"] + o[e] * cost["spread_bps"] * 1e-4
        sl = cost["slip"] + o[e] * cost["slip_bps"] * 1e-4
        fill = o[e] + sp + sl
        risk = K_STOP * atr[i]
        stop = fill - risk
        target_days = int(hold[k % len(hold)]) if hold is not None else None
        k += 1
        j, px = e, None
        while j < n:
            slj = cost["slip"] + o[j] * cost["slip_bps"] * 1e-4
            if l[j] <= stop:
                px = min(stop, o[j]) - slj; break
            days = j - e + 1
            if (hold is None and (rsi[j] > EXIT_RSI or days >= MAX_DAYS)) or (hold is not None and days >= target_days):
                px = c[j] - slj; break
            j += 1
        if px is None:
            break
        nights = j - e
        r = (px - fill - nights * fill * cost["swap_bps"] * 1e-4) / risk
        r_g = ((c[j] if px > stop else stop) - o[e]) / risk
        out.append((int(ts[e]), int(ts[j]) + 86_399_000, r, r_g, j - e + 1))
        i = j + 1
    return out


def weekly_t(df: pd.DataFrame) -> float:
    w = df.groupby(df.entry_ts // (7 * 86_400_000))["r"].sum()
    return float(w.mean() / (w.std(ddof=1) / np.sqrt(len(w)))) if len(w) > 2 else np.nan


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    data = {s: load_daily(s) for g in GROUPS.values() for s in g}
    summary = []
    for gname, members in GROUPS.items():
        real_rows, per_sym, ctx = [], [], {}
        for s, cost in members.items():
            x = data[s]
            up = (x["c"] > x["sma"]) & ~np.isnan(x["sma"])
            real_mask = up & (x["rsi"] < THR)
            tr = pd.DataFrame(simulate(x, real_mask, cost), columns=["entry_ts", "exit_ts", "r", "r_gross", "days"])
            tr["symbol"] = s
            real_rows.append(tr)
            ctx[s] = (x, up, real_mask.sum() / max(up.sum(), 1), tr["days"].to_numpy() if len(tr) else np.array([3]), cost)
            per_sym.append(dict(symbol=s, first=pd.to_datetime(x["ts"][0], unit="ms").date(), **dict(zip(
                ["n", "win", "avgR", "PF"], [len(tr), round(100 * (tr.r > 0).mean(), 1) if len(tr) else np.nan,
                                              round(tr.r.mean(), 3) if len(tr) else np.nan,
                                              round(tr.r[tr.r > 0].sum() / -tr.r[tr.r < 0].sum(), 2) if (tr.r < 0).any() else np.nan]))))
        real = pd.concat(real_rows, ignore_index=True)
        real_mean = real.r.mean()
        # controls
        c1, c2 = [], []
        all_days = np.unique(np.concatenate([v[0]["ts"] for v in ctx.values()]))
        for _ in range(REPS):
            u = pd.Series(rng.random(len(all_days)), index=all_days)   # shared across instruments
            r1, r2 = [], []
            for s, (x, up, dens, holds, cost) in ctx.items():
                m = up & (u.reindex(x["ts"]).to_numpy() < dens)
                r1 += [t[2] for t in simulate(x, m, cost)]
                r2 += [t[2] for t in simulate(x, m, cost, hold=rng.permutation(holds))]
            c1.append(np.mean(r1)); c2.append(np.mean(r2))
        c1, c2 = np.array(c1), np.array(c2)
        m = full_metrics(real)
        print(f"\n################ {gname}  ({len(members)} instruments) ################")
        print(pd.DataFrame(per_sym).to_string(index=False))
        print("pooled:", m)
        print(f"t-stat (per trade) {real_mean / (real.r.std(ddof=1) / np.sqrt(len(real))):.2f} | "
              f"t-stat (weekly-clustered) {weekly_t(real):.2f} | positive instruments "
              f"{sum(1 for p in per_sym if p['n'] and p['avgR'] > 0)}/{sum(1 for p in per_sym if p['n'])}")
        print(f"control C1 (random uptrend entry, same exits): mean {c1.mean():+.3f}R  "
              f"[5-95%: {np.percentile(c1, 5):+.3f}, {np.percentile(c1, 95):+.3f}]  p(real<=random) = {(c1 >= real_mean).mean():.3f}")
        print(f"control C2 (random uptrend entry, matched holding): mean {c2.mean():+.3f}R  "
              f"[5-95%: {np.percentile(c2, 5):+.3f}, {np.percentile(c2, 95):+.3f}]  p = {(c2 >= real_mean).mean():.3f}")
        print("by year:", " | ".join(f"{y}: {g.r.mean():+.2f} ({len(g)})" for y, g in real.groupby(pd.to_datetime(real.entry_ts, unit='ms').dt.year)))
        print("costs x0 (gross):", brief(real.r_gross))
        real.to_csv(f"out_validate_{gname}.csv", index=False)
        summary.append(dict(group=gname, n=len(real), win=m["win"], expR=m["expectancy_R"], PF=m["PF"],
                            maxDD=m["maxDD_pct"], t_weekly=round(weekly_t(real), 2),
                            C1_mean=round(c1.mean(), 3), p_C1=round((c1 >= real_mean).mean(), 3),
                            C2_mean=round(c2.mean(), 3), p_C2=round((c2 >= real_mean).mean(), 3)))
    print("\nsplit adjustments applied:", SPLIT_LOG)
    print("\n=== SUMMARY ===")
    print(pd.DataFrame(summary).to_string(index=False))
    pd.DataFrame(summary).to_csv("out_validate_summary.csv", index=False)
