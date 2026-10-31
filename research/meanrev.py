"""Phase E: best robust alternative — daily short-term mean reversion (RSI(2) pullback in trend).

Rules (pre-specified before testing; well documented in public literature since the 2000s):
  Long : close > SMA200 and RSI(2) < thr          -> buy next day's open
  Short: close < SMA200 and RSI(2) > 100 - thr    -> sell next day's open   (reported separately)
  Exit : first of (a) exit rule on a daily close, (b) protective stop k_stop * ATR(14) from entry
         (checked intraday, stop assumed hit before any exit), (c) 10-day time stop at close.
  R    : initial risk = k_stop * ATR at entry, measured from the actual fill.
Free parameters (3): thr {5, 10, 15}, exit_rule {sma5, rsi50, rsi70}, k_stop {2.0, 3.0}.
Costs: same per-instrument spread/slippage/swap as universe.py (swap per night held).

Validation: anchored walk-forward (train = all data before the test window), test windows
2015-16, 2017-18, 2019-20, 2021-22, final untouched HOLDOUT 2023-01-01..2026-10-21.
Selection on train only: highest expectancy among settings with train win-rate >= 70%
(track A) and highest expectancy overall (track B); per asset scope "all" and "indices+metals"
(long-side only), because the effect is documented mainly for equity indices.
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd

from robustness import brief, full_metrics, monte_carlo, regime_table
from universe import UNIVERSE, cost_kwargs, data_path

GRID = dict(thr=[5, 10, 15], exit_rule=["sma5", "rsi50", "rsi70"], k_stop=[2.0, 3.0])
COMBOS = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]


def ms(y, m=1, d=1):
    return pd.Timestamp(year=y, month=m, day=d).value // 1_000_000


TESTS = [(ms(y), ms(y + 2), f"{y}-{y + 1}") for y in (2015, 2017, 2019, 2021)]
HOLDOUT = (ms(2023), ms(2026, 10, 22), "HOLDOUT 2023-26")


def daily_bars(sym: str) -> pd.DataFrame:
    h = pd.read_csv(data_path(sym), index_col="timestamp")
    day = h.index // 86_400_000 * 86_400_000
    g = h.groupby(day)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "bars": g["open"].size()})
    return d[d.bars >= 12]          # drop thin weekend/holiday stubs


def rsi_wilder(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def run_mr(d: pd.DataFrame, sym: str, thr: float, exit_rule: str, k_stop: float, cost_mult: float = 1.0,
           delay: int = 0, max_days: int = 10) -> pd.DataFrame:
    ck = cost_kwargs(sym, cost_mult)
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    ts = d.index.to_numpy()
    cs = d["close"]
    sma200 = cs.rolling(200).mean().to_numpy()
    sma5 = cs.rolling(5).mean().to_numpy()
    rsi = rsi_wilder(cs, 2).to_numpy()
    prev_c = cs.shift(1)
    tr_ = np.maximum(d["high"] - d["low"], np.maximum((d["high"] - prev_c).abs(), (d["low"] - prev_c).abs()))
    atr = tr_.ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    out = []
    i, n = 201, len(d)
    while i < n - 1 - delay:
        sig = 0
        if c[i] > sma200[i] and rsi[i] < thr:
            sig = 1
        elif c[i] < sma200[i] and rsi[i] > 100 - thr:
            sig = -1
        if sig == 0:
            i += 1
            continue
        e = i + 1 + delay
        sp = ck["spread"] + o[e] * ck["spread_bps"] * 1e-4
        sl_ = ck["slip"] + o[e] * ck["slip_bps"] * 1e-4
        fill = o[e] + sp + sl_ if sig == 1 else o[e] - sl_
        stop = fill - sig * k_stop * atr[i]
        risk = k_stop * atr[i]
        j, px, outcome = e, None, ""
        while j < n:
            spj = ck["spread"] + o[j] * ck["spread_bps"] * 1e-4
            slj = ck["slip"] + o[j] * ck["slip_bps"] * 1e-4
            adv = l[j] if sig == 1 else h[j] + spj
            opn = o[j] if sig == 1 else o[j] + spj
            if (sig == 1 and adv <= stop) or (sig == -1 and adv >= stop):
                px = (min(stop, opn) if sig == 1 else max(stop, opn)) - sig * slj
                outcome = "STOP"; break
            cl = c[j] if sig == 1 else c[j] + spj
            done = ((exit_rule == "sma5" and ((sig == 1 and c[j] > sma5[j]) or (sig == -1 and c[j] < sma5[j]))) or
                    (exit_rule == "rsi50" and ((sig == 1 and rsi[j] > 50) or (sig == -1 and rsi[j] < 50))) or
                    (exit_rule == "rsi70" and ((sig == 1 and rsi[j] > 70) or (sig == -1 and rsi[j] < 30))))
            if done or j - e + 1 >= max_days:
                px = cl - sig * slj
                outcome = "EXIT" if done else "TIME"; break
            j += 1
        if px is None:
            break
        nights = j - e
        swap = nights * fill * ck["swap_bps"] * 1e-4
        r = ((px - fill) * sig - swap) / risk
        r_gross = ((c[j] if outcome != "STOP" else stop) - o[e]) * sig / risk
        out.append(dict(symbol=sym, direction=sig, entry_ts=int(ts[e]), exit_ts=int(ts[j]) + 86_399_000,
                        r=r, r_gross=r_gross, outcome=outcome, nights=nights))
        i = j + 1                     # one position at a time per instrument
    return pd.DataFrame(out)


SCOPES = {
    "all_long_short": (list(UNIVERSE), (1, -1)),
    "all_long_only": (list(UNIVERSE), (1,)),
    "idx_metals_long": (["usa500idxusd", "usatechidxusd", "deuidxeur", "xauusd", "xagusd"], (1,)),
}


def agg(r: pd.Series) -> dict:
    if len(r) == 0:
        return dict(n=0, win=np.nan, avgR=np.nan, PF=np.nan)
    gl = -r[r < 0].sum()
    return dict(n=len(r), win=100 * (r > 0).mean(), avgR=r.mean(), PF=r[r > 0].sum() / gl if gl > 0 else np.inf)


if __name__ == "__main__":
    dailies = {s: daily_bars(s) for s in UNIVERSE}
    allt = []
    for cid, cb in enumerate(COMBOS):
        for s, d in dailies.items():
            t = run_mr(d, s, **cb)
            if not t.empty:
                allt.append(t.assign(combo=cid))
    grid = pd.concat(allt, ignore_index=True)
    grid.to_pickle("out_mr_grid.pkl")
    pd.set_option("display.width", 250)
    sel_rows, oos = [], {}
    for scope, (syms, dirs) in SCOPES.items():
        g = grid[grid.symbol.isin(syms) & grid.direction.isin(dirs)]
        for track in ("A", "B"):
            parts = []
            for (te0, te1, label) in TESTS + [HOLDOUT]:
                train = g[g.entry_ts < te0]
                rows = [dict(combo=cid, **agg(x["r"])) for cid, x in train.groupby("combo")]
                t = pd.DataFrame(rows)
                t = t[t.n >= 100]
                cand = t[t.win >= 70] if track == "A" else t
                flagged = cand.empty
                pick = (t.sort_values("win", ascending=False) if flagged else cand.sort_values("avgR", ascending=False)).iloc[0]
                cid = int(pick.combo)
                test = g[(g.combo == cid) & (g.entry_ts >= te0) & (g.entry_ts < te1)]
                a = agg(test["r"])
                sel_rows.append(dict(scope=scope, track=track, window=label, **COMBOS[cid], flagged_no70=flagged,
                                     IS_n=int(pick.n), IS_win=round(pick.win, 1), IS_avgR=round(pick.avgR, 3),
                                     OOS_n=a["n"], OOS_win=round(a["win"], 1), OOS_avgR=round(a["avgR"], 3),
                                     OOS_PF=round(a["PF"], 2),
                                     WFE=round(a["avgR"] / pick.avgR, 2) if pick.avgR > 0 else None))
                parts.append(test.assign(window=label))
            oos[(scope, track)] = pd.concat(parts, ignore_index=True)
    sel = pd.DataFrame(sel_rows)
    sel.to_csv("out_mr_selection.csv", index=False)
    print(sel.to_string(index=False))
    print("\n=== pooled walk-forward OOS (2015-2022) and HOLDOUT (2023-26) ===")
    for k, t in oos.items():
        wf, ho = t[t.window != HOLDOUT[2]], t[t.window == HOLDOUT[2]]
        print(f"{k[0]:16s} {k[1]} WF: {brief(wf['r'])} | HOLDOUT: {brief(ho['r'])}")
        t.to_csv(f"out_mr_oos_{k[0]}_{k[1]}.csv", index=False)
