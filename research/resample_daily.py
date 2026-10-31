"""Resample Dukascopy M1 CSVs to daily (UTC day) bars, chunked.
Thin days (< 25% of the instrument's median minutes per day, e.g. Sunday-evening stubs) are dropped.

    python resample_daily.py <out_dir> <m1_csv> [<m1_csv> ...]
Output: <out_dir>/<symbol>.csv with timestamp(ms, day start), open, high, low, close, n
"""
import os
import sys

import pandas as pd

out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)
for src in sys.argv[2:]:
    sym = os.path.basename(src).split("_m1_")[0]
    parts = []
    for ch in pd.read_csv(src, chunksize=1_000_000, usecols=["timestamp", "open", "high", "low", "close"]):
        b = ch["timestamp"] // 86_400_000 * 86_400_000
        g = ch.groupby(b, sort=True)
        parts.append(pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                                   "close": g["close"].last(), "n": g["open"].size()}))
    df = pd.concat(parts)
    g = df.groupby(level=0, sort=True)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "n": g["n"].sum()})
    d = d[d.n >= 0.25 * d.n.median()]
    d.index.name = "timestamp"
    d.to_csv(f"{out_dir}/{sym}.csv")
    print(sym, len(d), pd.to_datetime(d.index[0], unit="ms").date(), pd.to_datetime(d.index[-1], unit="ms").date(), flush=True)
