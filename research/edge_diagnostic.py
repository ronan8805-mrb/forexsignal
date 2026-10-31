"""Phase A + B: baseline on the full universe, then an entry-edge diagnostic.

E-ratio = mean(max favourable excursion) / mean(max adverse excursion) over the next N bars
after entry, both in ATR units, measured from the next bar's open (gross of costs).
A ratio of 1.0 means the entry is no better than a coin flip. We compare the strategy's
entries against random entries drawn from the same instrument, same session hours,
random direction (20 random samples per real signal).
Also reports 'hit': share of signals whose close after N bars is in the trade direction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from atr_ew_backtest import Params, indicators, load, run, stats
from universe import UNIVERSE, cost_kwargs, data_path

HORIZONS = (5, 10, 20)
rng = np.random.default_rng(7)


def excursions(o, h, l, c, idx, direction, atr, n):
    """MFE/MAE in ATR from open[idx] over bars idx..idx+n-1; close-direction hit at idx+n-1."""
    mfe, mae, hit = [], [], []
    for i, d, a in zip(idx, direction, atr):
        j = i + n
        if j > len(o) or a <= 0 or np.isnan(a):
            continue
        e = o[i]
        hi, lo = h[i:j].max(), l[i:j].min()
        if d == 1:
            mfe.append((hi - e) / a); mae.append((e - lo) / a); hit.append(c[j - 1] > e)
        else:
            mfe.append((e - lo) / a); mae.append((hi - e) / a); hit.append(c[j - 1] < e)
    mfe, mae = np.array(mfe), np.array(mae)
    return mfe.mean() / mae.mean(), np.mean(hit), len(mfe)


if __name__ == "__main__":
    base_rows, edge_rows = [], []
    for sym in UNIVERSE:
        df = load(data_path(sym))
        p = Params(**cost_kwargs(sym))
        tr = run(df, p)
        s, g = stats(tr["r"]), stats(tr["r_gross"])
        yrs = (df.index[-1] - df.index[0]) / (365.25 * 86_400_000)
        base_rows.append(dict(symbol=sym, n=s["n"], per_yr=round(s["n"] / yrs), win=s["win"], avgR=s["avgR"],
                              PF=s["PF"], totalR=s["totalR"], avgR_gross=g["avgR"], PF_gross=g["PF"]))
        # entry-edge diagnostic
        ts = df.index.to_numpy()
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        atr = indicators(df, p)["atr"]
        pos = np.searchsorted(ts, tr["entry_ts"].to_numpy())
        sig_atr = tr["atr"].to_numpy()
        minute = (ts // 60_000) % 1440
        pool = np.where((minute >= 7 * 60) & (minute < 20 * 60) & ~np.isnan(atr))[0]
        pool = pool[pool > 300]
        k = len(pos) * 20
        r_idx = rng.choice(pool, size=k) + 1          # enter at next bar open, like a signal
        r_idx = r_idx[r_idx < len(o)]
        r_dir = rng.choice([-1, 1], size=len(r_idx))
        row = dict(symbol=sym, signals=len(pos))
        for n in HORIZONS:
            e_sig, hit_sig, _ = excursions(o, h, l, c, pos, tr["direction"].to_numpy(), sig_atr, n)
            e_rnd, hit_rnd, _ = excursions(o, h, l, c, r_idx, r_dir, atr[r_idx - 1], n)
            row[f"E{n}_sig"], row[f"E{n}_rnd"] = round(e_sig, 3), round(e_rnd, 3)
            row[f"hit{n}_sig"], row[f"hit{n}_rnd"] = round(100 * hit_sig, 1), round(100 * hit_rnd, 1)
        edge_rows.append(row)
        print("done", sym, flush=True)

    pd.set_option("display.width", 250)
    b = pd.DataFrame(base_rows)
    e = pd.DataFrame(edge_rows)
    print("\n=== BASELINE (default settings, H1, net of costs) ===")
    print(b.to_string(index=False))
    print(f"pooled: trades {b.n.sum()}, trade-weighted avgR {(b.avgR * b.n).sum() / b.n.sum():.3f}, "
          f"gross {(b.avgR_gross * b.n).sum() / b.n.sum():.3f}")
    print("\n=== ENTRY EDGE: E-ratio and direction hit-rate, signal vs random ===")
    print(e.to_string(index=False))
    w = e.signals / e.signals.sum()
    for n in HORIZONS:
        print(f"pooled N={n}: E_sig {(e[f'E{n}_sig'] * w).sum():.3f} vs E_rnd {(e[f'E{n}_rnd'] * w).sum():.3f} | "
              f"hit_sig {(e[f'hit{n}_sig'] * w).sum():.1f}% vs hit_rnd {(e[f'hit{n}_rnd'] * w).sum():.1f}%")
    b.to_csv("out_baseline.csv", index=False)
    e.to_csv("out_edge.csv", index=False)
