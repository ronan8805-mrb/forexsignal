"""Cut Dukascopy M1 bid bars to a UTC hour window that covers a market's cash session in both DST states.
Run where the M1 exports live (it reads a CSV and writes a compact .npz for vwap_test.py):

    python vwap_extract.py usa500idxusd_m1_*.csv data/vwap/US500.npz 13 21.5
Windows used (UTC hours): US500/NAS100/US30 13-21.5 · DE40, UK100 6.5-16.75 · JP225 23.5-6.5 · XAUUSD 6.5-17.25
"""
import sys
import time

import numpy as np
import pandas as pd

src, out, h0, h1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
t0, parts = time.time(), []
for ch in pd.read_csv(src, chunksize=2_000_000, engine="c"):
    hod = (ch["timestamp"].to_numpy() // 60000) % 1440 / 60.0
    m = ((hod >= h0) & (hod < h1)) if h0 < h1 else ((hod >= h0) | (hod < h1))
    parts.append(ch[m])
d = pd.concat(parts)
np.savez_compressed(out, ts=d.timestamp.to_numpy("int64"), o=d.open.to_numpy(), h=d.high.to_numpy(),
                    l=d.low.to_numpy(), c=d.close.to_numpy(), v=d.volume.to_numpy("float32"))
print(out.split("/")[-1], len(d), "rows", round(time.time() - t0), "s")
