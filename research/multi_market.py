"""Out-of-market validation: run the default settings and the gold-derived candidate,
UNCHANGED, on every instrument in research/data. Costs per instrument are typical
retail ECN round-trip spread+commission plus slippage (price units).

    python multi_market.py            # from the research folder
"""
from __future__ import annotations

import os
from dataclasses import replace

import pandas as pd

from atr_ew_backtest import Params, load, run, stats

COSTS = {  # symbol: (spread incl. commission, slippage)
    "xauusd": (0.30, 0.05), "xagusd": (0.025, 0.005),
    "eurusd": (0.00010, 0.00002), "gbpusd": (0.00014, 0.00003), "audusd": (0.00012, 0.00002),
    "nzdusd": (0.00018, 0.00003), "usdcad": (0.00016, 0.00003), "usdchf": (0.00015, 0.00003),
    "usdjpy": (0.012, 0.003),
}
CANDIDATE = dict(trig_frac=0.5, min_swing_atr=1.5, sl_mode="tighter", w3_trend_req=True, min_adx=25)
SPLIT = pd.Timestamp("2019-01-01").value // 1_000_000


def path(sym: str, minutes: int) -> str:
    p = f"data/{sym}/xauusd_m{minutes}.csv"  # resample.py names files xauusd_* regardless of symbol
    return p if os.path.exists(p) else f"data/xauusd_m{minutes}.csv"


def row(sym: str, cfg: str, tf: str, tr: pd.DataFrame, years: float) -> dict:
    if tr.empty:
        return dict(symbol=sym, cfg=cfg, tf=tf, n=0)
    s, g = stats(tr["r"]), stats(tr["r_gross"])
    ins, oos = tr[tr.entry_ts < SPLIT]["r"], tr[tr.entry_ts >= SPLIT]["r"]
    lo, sh = tr[tr.direction == 1]["r"], tr[tr.direction == -1]["r"]
    return dict(symbol=sym, cfg=cfg, tf=tf, n=s["n"], per_yr=round(s["n"] / years, 0), win=s["win"],
                avgR=s["avgR"], PF=s["PF"], totalR=s["totalR"], maxDD=s["maxDD_R"], avgR_gross=g["avgR"],
                IS_avgR=round(ins.mean(), 3) if len(ins) else None, OOS_avgR=round(oos.mean(), 3) if len(oos) else None,
                long_avgR=round(lo.mean(), 3) if len(lo) else None, short_avgR=round(sh.mean(), 3) if len(sh) else None)


if __name__ == "__main__":
    rows = []
    for sym, (spread, slip) in COSTS.items():
        base = Params(spread=spread, slip=slip)
        for tf, m in (("M15", 15), ("H1", 60)):
            f = path(sym, m)
            if sym != "xauusd" and not os.path.exists(f"data/{sym}"):
                continue
            df = load(f)
            years = (df.index[-1] - df.index[0]) / (365.25 * 86_400_000)
            rows.append(row(sym, "default", tf, run(df, base), years))
            if tf == "H1":
                rows.append(row(sym, "candidate", tf, run(df, replace(base, **CANDIDATE)), years))
        print("done", sym, flush=True)
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(res.to_string(index=False))
    res.to_csv("multi_market_results.csv", index=False)
    cand = res[(res.cfg == "candidate")]
    print("\nCandidate on markets it was NOT tuned on (excluding xauusd):")
    oth = cand[cand.symbol != "xauusd"]
    print(f"  positive net avgR: {(oth.avgR > 0).sum()}/{len(oth)}  | pooled trades {oth.n.sum()} "
          f"| trade-weighted avgR {(oth.avgR * oth.n).sum() / oth.n.sum():.3f}")
