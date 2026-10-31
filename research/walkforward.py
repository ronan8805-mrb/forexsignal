"""Phase C: walk-forward optimisation (anti-overfitting protocol).

Free parameters (4):
  tp_single_r  : single take-profit in R  {0.3, 0.5, 0.7, 1.0} or None (= original 3-step ladder)
  time_stop    : bars after entry          {0 (off), 24}
  min_adx      : trend-strength gate       {18, 25}
  trig_frac    : entry trigger level       {0.5, 1.0}
Everything else stays at the Pine defaults. The SAME parameters are used for every instrument
(portfolio-level optimisation, no per-pair tuning).

Windows (chronological, rolling 6-year train / 2-year test):
  train 2007-12 -> test 2013-14,  train 2009-14 -> test 2015-16,  train 2011-16 -> test 2017-18,
  train 2013-18 -> test 2019-20,  train 2015-20 -> test 2021-22
  FINAL HOLDOUT: train 2017-22 -> test 2023-01-01 .. 2026-10-21 (looked at once, at the end)

Two selection tracks, decided on TRAIN data only:
  A "70% target": highest train expectancy among settings with train win-rate >= 70% (n >= 150);
                  if none reach 70%, the highest-win-rate setting is taken and flagged.
  B "robust"    : highest train expectancy, no win-rate constraint (n >= 150).

Outputs: out_grid_trades.pkl (every trade of every variant = full variant log),
         out_grid_variants.csv, out_wf_selection.csv, out_wf_oos_trades_{A,B}.csv
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
from dataclasses import replace

import numpy as np
import pandas as pd

from atr_ew_backtest import Params, load, run
from universe import UNIVERSE, cost_kwargs, data_path

GRID = dict(tp_single_r=[0.3, 0.5, 0.7, 1.0, None], time_stop=[0, 24], min_adx=[18, 25], trig_frac=[0.5, 1.0])
COMBOS = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]


def ms(y, m=1, d=1):
    return pd.Timestamp(year=y, month=m, day=d).value // 1_000_000


WINDOWS = [(ms(a), ms(a + 6), ms(a + 6), ms(a + 8), f"{a + 6}-{a + 7}") for a in (2007, 2009, 2011, 2013, 2015)]
HOLDOUT = (ms(2017), ms(2023), ms(2023), ms(2026, 10, 22), "HOLDOUT 2023-26")
MIN_TRAIN_N = 150


def _run_symbol(sym: str) -> pd.DataFrame:
    df = load(data_path(sym))
    out = []
    for cid, combo in enumerate(COMBOS):
        p = replace(Params(**cost_kwargs(sym)), **combo)
        tr = run(df, p)
        if tr.empty:
            continue
        tr["combo"], tr["symbol"] = cid, sym
        out.append(tr)
    print("grid done", sym, flush=True)
    return pd.concat(out, ignore_index=True)


def agg(tr: pd.DataFrame) -> dict:
    r = tr["r"]
    if len(r) == 0:
        return dict(n=0, win=np.nan, avgR=np.nan, PF=np.nan)
    gl = -r[r < 0].sum()
    return dict(n=len(r), win=100 * (r > 0).mean(), avgR=r.mean(), PF=r[r > 0].sum() / gl if gl > 0 else np.inf)


def select(train: pd.DataFrame, track: str) -> tuple[int, dict, bool]:
    rows = []
    for cid, g in train.groupby("combo"):
        a = agg(g)
        if a["n"] >= MIN_TRAIN_N:
            rows.append(dict(combo=cid, **a))
    t = pd.DataFrame(rows)
    flagged = False
    if track == "A":
        ok = t[t.win >= 70]
        if ok.empty:
            pick, flagged = t.sort_values("win", ascending=False).iloc[0], True
        else:
            pick = ok.sort_values("avgR", ascending=False).iloc[0]
    else:
        pick = t.sort_values("avgR", ascending=False).iloc[0]
    return int(pick.combo), pick.to_dict(), flagged


def neighbours(cid: int) -> list[int]:
    """Settings that differ from cid by one step in exactly one parameter (plateau check)."""
    c = COMBOS[cid]
    out = []
    for k, vals in GRID.items():
        i = vals.index(c[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                n = dict(c, **{k: vals[j]})
                out.append(COMBOS.index(n))
    return out


if __name__ == "__main__":
    with mp.Pool(2) as pool:
        parts = pool.map(_run_symbol, list(UNIVERSE))
    grid = pd.concat(parts, ignore_index=True)
    grid.to_pickle("out_grid_trades.pkl")

    # full variant log (whole history, for reference only - NOT used for selection)
    vlog = []
    for cid, g in grid.groupby("combo"):
        vlog.append(dict(combo=cid, **COMBOS[cid], **agg(g)))
    pd.DataFrame(vlog).to_csv("out_grid_variants.csv", index=False)

    sel_rows = []
    oos = {"A": [], "B": []}
    for (tr0, tr1, te0, te1, label) in WINDOWS + [HOLDOUT]:
        train = grid[(grid.entry_ts >= tr0) & (grid.entry_ts < tr1)]
        test = grid[(grid.entry_ts >= te0) & (grid.entry_ts < te1)]
        for track in ("A", "B"):
            cid, is_stats, flagged = select(train, track)
            nb = [agg(train[train.combo == j])["avgR"] for j in neighbours(cid)]
            o = test[test.combo == cid]
            oa = agg(o)
            sel_rows.append(dict(window=label, track=track, combo=cid, **{f"p_{k}": v for k, v in COMBOS[cid].items()},
                                 flagged_no70=flagged, IS_n=is_stats["n"], IS_win=round(is_stats["win"], 1),
                                 IS_avgR=round(is_stats["avgR"], 3), IS_PF=round(is_stats["PF"], 2),
                                 nbr_avgR_mean=round(float(np.nanmean(nb)), 3), nbr_avgR_min=round(float(np.nanmin(nb)), 3),
                                 OOS_n=oa["n"], OOS_win=round(oa["win"], 1), OOS_avgR=round(oa["avgR"], 3),
                                 OOS_PF=round(oa["PF"], 2),
                                 WFE=round(oa["avgR"] / is_stats["avgR"], 2) if is_stats["avgR"] > 0 else None))
            o = o.copy(); o["window"] = label
            oos[track].append(o)
    sel = pd.DataFrame(sel_rows)
    sel.to_csv("out_wf_selection.csv", index=False)
    pd.set_option("display.width", 260)
    print(sel.to_string(index=False))
    for track in ("A", "B"):
        allo = pd.concat(oos[track], ignore_index=True)
        allo.to_csv(f"out_wf_oos_trades_{track}.csv", index=False)
        wf = allo[allo.window != HOLDOUT[4]]
        ho = allo[allo.window == HOLDOUT[4]]
        print(f"\nTrack {track}: walk-forward OOS 2013-2022 {agg(wf)} | HOLDOUT 2023-26 {agg(ho)}")
