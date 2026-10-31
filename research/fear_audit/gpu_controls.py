"""Fear audit, part 3: large-scale controls on the GPU (PyTorch/CUDA). Diagnostic; reads out_trades_all.csv.

  1. Trade-level random veto (pre-registered null), 100,000 runs.
  2. Date-clustered random veto (veto whole signal days, like real fear days), 100,000 runs.
  3. Stationary block bootstrap of the daily P&L (20-day blocks), 100,000 resamples:
     confidence interval for the Sharpe gain (filter on minus off), resampling both series on the same days.

    cd research/fear_audit && python gpu_controls.py [runs]
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
BATCH = 5_000
RISK = 0.005
dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
gen = torch.Generator(device=dev).manual_seed(20261031)
print("device:", torch.cuda.get_device_name(0) if dev.type == "cuda" else "CPU", "| runs:", RUNS, flush=True)

T = pd.read_csv("out_trades_all.csv", parse_dates=["exit_date", "sig_date"])
days = pd.bdate_range(T.exit_date.min(), T.exit_date.max())
D, N = len(days), len(T)
idx = torch.tensor(days.searchsorted(T.exit_date), device=dev)
w = torch.tensor(T.r.to_numpy() * RISK, device=dev, dtype=torch.float64)
veto = torch.tensor(T.veto.to_numpy(), device=dev)
k = int(veto.sum())


def pnl(keep: torch.Tensor) -> torch.Tensor:
    """keep: (B, N) 0/1 -> daily P&L (B, D)."""
    out = torch.zeros(keep.shape[0], D, device=dev, dtype=torch.float64)
    return out.scatter_add_(1, idx.expand(keep.shape[0], N), w * keep)


def sharpe(p: torch.Tensor) -> torch.Tensor:
    return (252 ** 0.5) * p.mean(1) / p.std(1)


base = torch.ones(1, N, device=dev, dtype=torch.float64)
on = (~veto).double().unsqueeze(0)
s_off, s_on = sharpe(pnl(base)).item(), sharpe(pnl(on)).item()
print(f"Sharpe off {s_off:.4f} | on {s_on:.4f} | vetoed {k} of {N}", flush=True)

# 1. trade-level random veto
t0 = time.time(); res = []
for b in range(0, RUNS, BATCH):
    B = min(BATCH, RUNS - b)
    r = torch.rand(B, N, device=dev, generator=gen)
    thr = torch.kthvalue(r, k, dim=1).values.unsqueeze(1)        # exactly k smallest -> vetoed
    res.append(sharpe(pnl((r > thr).double())))
s1 = torch.cat(res)
p1 = (s1 >= s_on).double().mean().item()
print(f"[1] trade-level random veto: mean {s1.mean():.4f}, 95th pct {torch.quantile(s1.float(), 0.95):.4f}, "
      f"p = {p1:.4f}  ({time.time() - t0:.1f}s)", flush=True)

# 2. date-clustered random veto: random order of signal days, veto whole days until k trades are reached
codes, uniq = pd.factorize(T.sig_date)
tdi = torch.tensor(codes, device=dev)
cnt = torch.bincount(tdi).double()
ND = len(uniq)
t0 = time.time(); res, nv = [], []
for b in range(0, RUNS, BATCH):
    B = min(BATCH, RUNS - b)
    perm = torch.argsort(torch.rand(B, ND, device=dev, generator=gen), dim=1)
    cum = torch.cumsum(cnt[perm], dim=1)
    chosen = (cum <= k).double()                                # whole days, never more than k trades
    vd = torch.zeros(B, ND, device=dev, dtype=torch.float64).scatter_(1, perm, chosen)
    keep = 1.0 - vd[:, tdi]
    nv.append((N - keep.sum(1)))
    res.append(sharpe(pnl(keep)))
s2, nv = torch.cat(res), torch.cat(nv)
p2 = (s2 >= s_on).double().mean().item()
print(f"[2] date-clustered random veto: mean {s2.mean():.4f}, 95th pct {torch.quantile(s2.float(), 0.95):.4f}, "
      f"p = {p2:.4f} | trades vetoed per run: mean {nv.mean():.0f} (filter {k})  ({time.time() - t0:.1f}s)", flush=True)

# 3. stationary block bootstrap of the Sharpe gain (paired: same resampled days for on and off)
P = torch.cat([pnl(base), pnl(on)])                             # (2, D)
L = 20
t0 = time.time(); res = []
for b in range(0, RUNS, BATCH):
    B = min(BATCH, RUNS - b)
    nb = D // L + 1
    starts = torch.randint(0, D, (B, nb), device=dev, generator=gen)
    pos = (starts.unsqueeze(2) + torch.arange(L, device=dev)).reshape(B, -1)[:, :D] % D
    off_b, on_b = P[0][pos], P[1][pos]
    res.append(sharpe(on_b) - sharpe(off_b))
g = torch.cat(res).float()
lo, hi = torch.quantile(g, 0.025).item(), torch.quantile(g, 0.975).item()
print(f"[3] block bootstrap (20-day blocks) Sharpe gain: point {s_on - s_off:+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}], "
      f"P(gain <= 0) = {(g <= 0).float().mean():.4f}  ({time.time() - t0:.1f}s)", flush=True)

pd.DataFrame([dict(runs=RUNS, sharpe_off=s_off, sharpe_on=s_on, p_trade_level=p1, p_date_clustered=p2,
                   boot_gain_lo=lo, boot_gain_hi=hi, boot_p_gain_le0=float((g <= 0).float().mean()),
                   device=torch.cuda.get_device_name(0) if dev.type == "cuda" else "CPU")]).to_csv("out_gpu_controls.csv", index=False)
