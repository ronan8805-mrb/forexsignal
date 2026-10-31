"""Pre-registered test of three intraday VWAP setups (rules: research/VWAP_PREREG.md).

    python vwap_test.py            # main run + cost stress + random control + plateau  (writes out_vwap_*.csv)

Data: research/data/vwap/<MARKET>.npz  (Dukascopy M1 bid bars cut to a UTC window around the cash session;
made by vwap_extract.py from the M1 exports).
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

SESS = {  # market: (timezone, open minute, close minute) in local exchange time
    "US500": ("America/New_York", 570, 960), "NAS100": ("America/New_York", 570, 960),
    "US30": ("America/New_York", 570, 960), "DE40": ("Europe/Berlin", 540, 1050),
    "UK100": ("Europe/London", 480, 990), "JP225": ("Asia/Tokyo", 540, 900),
    "XAUUSD": ("Europe/London", 480, 1020),
}
COST_BPS = {"US500": 1.5, "NAS100": 1.2, "US30": 1.0, "DE40": 1.2, "UK100": 1.5, "JP225": 2.5, "XAUUSD": 1.5}
HOLDOUT = pd.Timestamp("2023-01-01").date()
RISK_PCT = 0.5
BASE = dict(A=dict(zone=0.2, tgt=2.0), B=dict(zone=0.2, tgt=2.0), C=dict(band=2.0, flat=0.5))
rng = np.random.default_rng(20261031)


# ---------------------------------------------------------------------------------------------- data
class Mkt:
    """Flat M1 arrays for one market (session minutes only) + M5 bars aligned to the session open."""

    def __init__(self, sym: str):
        tz, op, cl = SESS[sym]
        z = np.load(f"data/vwap/{sym}.npz")
        t = pd.to_datetime(z["ts"], unit="ms", utc=True).tz_convert(tz)
        mod = t.hour.to_numpy() * 60 + t.minute.to_numpy()
        keep = (mod >= op) & (mod < cl)
        date = np.asarray(t.date)[keep]
        mod = mod[keep] - op
        o, h, l, c, v = (z[k][keep].astype(float) for k in "ohlcv")
        # drop thin days (< 80% of session minutes)
        d = pd.Series(date)
        cnt = d.map(d.value_counts())
        ok = (cnt >= 0.8 * (cl - op)).to_numpy()
        self.sym, self.L = sym, cl - op
        self.date, self.mod, self.o, self.h, self.l, self.c, self.v = date[ok], mod[ok], o[ok], h[ok], l[ok], c[ok], v[ok]
        n = len(self.o)
        # day boundaries
        new = np.r_[True, self.date[1:] != self.date[:-1]]
        self.dstart = np.flatnonzero(new)
        self.dend = np.r_[self.dstart[1:] - 1, n - 1]
        dayid = np.cumsum(new) - 1
        self.m1_day = dayid
        # session VWAP and sigma per M1 (inclusive of the bar)
        tp = (self.h + self.l + self.c) / 3
        w = np.where(self.v > 0, self.v, 0.0)
        cw = np.cumsum(w); cwp = np.cumsum(w * tp); cwp2 = np.cumsum(w * tp * tp)
        cn = np.arange(1, n + 1, dtype=float); cp = np.cumsum(tp); cp2 = np.cumsum(tp * tp)
        base = self.dstart[dayid] - 1
        def since_open(a):
            return a - np.where(base >= 0, a[np.maximum(base, 0)], 0.0)
        W, WP, WP2 = since_open(cw), since_open(cwp), since_open(cwp2)
        N, P, P2 = since_open(cn), since_open(cp), since_open(cp2)
        useeq = W <= 0
        vw = np.where(useeq, P / N, WP / np.where(useeq, 1, W))
        m2 = np.where(useeq, P2 / N, WP2 / np.where(useeq, 1, W))
        self.vw = vw
        self.sd = np.sqrt(np.maximum(m2 - vw * vw, 0.0))
        # M5 bars
        b = self.mod // 5
        key = dayid * 1000 + b
        newb = np.r_[True, key[1:] != key[:-1]]
        first = np.flatnonzero(newb)
        last = np.r_[first[1:] - 1, n - 1]
        self.b_first, self.b_last = first, last
        self.B = b[first]
        self.b_day = dayid[first]
        self.O5 = self.o[first]; self.C5 = self.c[last]
        self.H5 = np.maximum.reduceat(self.h, first); self.L5 = np.minimum.reduceat(self.l, first)
        self.V5 = vw[last]; self.S5 = self.sd[last]
        firstbar = np.r_[True, self.b_day[1:] != self.b_day[:-1]]
        pc = np.r_[np.nan, self.C5[:-1]]
        tr = np.where(firstbar, self.H5 - self.L5,
                      np.fmax(self.H5 - self.L5, np.fmax(np.abs(self.H5 - pc), np.abs(self.L5 - pc))))
        self.ATR = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
        self.ddate = self.date[self.dstart]
        self.cost = COST_BPS[sym] * 1e-4

    def sh(self, x, k):
        """x shifted by k M5 bars within the same day (NaN otherwise)."""
        out = np.full(len(x), np.nan)
        if k < len(x):
            out[k:] = x[:-k]
            same = np.r_[np.zeros(k, bool), self.b_day[k:] == self.b_day[:-k]]
            out[~same] = np.nan
        return out


# ---------------------------------------------------------------------------------------------- signals
def signals(m: Mkt, setup: str, p: dict) -> list[tuple]:
    """Returns (m5 index, direction, stop, kind) candidates, time-ordered."""
    C, O, H, L, V, S, A, B = m.C5, m.O5, m.H5, m.L5, m.V5, m.S5, m.ATR, m.B
    rng_ = H - L
    bull = (C > O) & (C >= L + 0.6 * rng_)
    bear = (C < O) & (C <= H - 0.6 * rng_)
    last_entry_b = (m.L - 60) // 5 - 1                 # entry at 5*(b+1) must be <= close - 60 min
    out = []
    if setup in ("A", "B"):
        early = B >= 5
        ok_t = early & (B <= last_entry_b)
        z, tg = p["zone"], p["tgt"]
        if setup == "A":
            s30 = V - m.sh(V, 6)
            ab = np.ones(len(C), bool); be = np.ones(len(C), bool)
            hh = np.full(len(C), -np.inf); ll = np.full(len(C), np.inf)
            for k in range(1, 7):
                ck, vk = m.sh(C, k), m.sh(V, k)
                ab &= ck > vk; be &= ck < vk
                hh = np.fmax(hh, m.sh(H, k)); ll = np.fmin(ll, m.sh(L, k))
            longs = ok_t & (s30 > 0) & ab & (hh >= V + 1.0 * A) & (L <= V + z * A) & (C > V) & bull
            shorts = ok_t & (s30 < 0) & be & (ll <= V - 1.0 * A) & (H >= V - z * A) & (C < V) & bear
        else:
            below = (C < V).astype(float); above = (C > V).astype(float)
            cu = (m.sh(C, 1) < m.sh(V, 1)) & (C > V)
            cd = (m.sh(C, 1) > m.sh(V, 1)) & (C < V)
            nb = sum(np.nan_to_num(m.sh(below, j), nan=0) for j in range(1, 7))
            na = sum(np.nan_to_num(m.sh(above, j), nan=0) for j in range(1, 7))
            # need 6 real bars before k
            full6 = ~np.isnan(m.sh(C, 6))
            reclaim_k = (cu & (nb >= 4) & full6).astype(float)
            reject_k = (cd & (na >= 4) & full6).astype(float)
            rk = np.zeros(len(C), bool); jk = np.zeros(len(C), bool)
            for j in range(2, 7):
                rk |= np.nan_to_num(m.sh(reclaim_k, j), nan=0) > 0
                jk |= np.nan_to_num(m.sh(reject_k, j), nan=0) > 0
            longs = ok_t & rk & (L <= V + z * A) & (C > V) & bull
            shorts = ok_t & jk & (H >= V - z * A) & (C < V) & bear
        for i in np.flatnonzero(longs | shorts):
            d = 1 if longs[i] else -1
            stop = (min(L[i], V[i]) - 0.2 * A[i]) if d == 1 else (max(H[i], V[i]) + 0.2 * A[i])
            out.append((i, d, stop, ("R", tg)))
    else:
        bd, fl = p["band"], p["flat"]
        ok_t = (B >= 11) & (B <= last_entry_b)
        flat = np.abs(V - m.sh(V, 12)) <= fl * A
        up, dn = V + bd * S, V - bd * S
        shorts = ok_t & flat & (S > 0) & (H >= up) & (C < up) & bear
        longs = ok_t & flat & (S > 0) & (L <= dn) & (C > dn) & bull
        for i in np.flatnonzero(longs | shorts):
            d = 1 if longs[i] else -1
            stop = (L[i] - 1.0 * A[i]) if d == 1 else (H[i] + 1.0 * A[i])
            out.append((i, d, stop, ("VWAP", None)))
    return out


# ---------------------------------------------------------------------------------------------- simulation
def sim(m: Mkt, e: int, d: int, stop: float, kind: tuple, cost: float) -> tuple | None:
    """Enter at M1 index e (open), direction d. Returns (exit_idx, R_net, R_gross) or None if invalid."""
    oe = m.o[e]
    if (d == 1 and oe <= stop) or (d == -1 and oe >= stop):
        return None
    risk = abs(oe - stop)
    end = m.dend[m.m1_day[e]]
    lo, hi = m.l[e:end + 1], m.h[e:end + 1]
    if d == 1:
        sh = lo <= stop
    else:
        sh = hi >= stop
    if kind[0] == "R":
        tgt = oe + d * kind[1] * risk
        th = (hi >= tgt) if d == 1 else (lo <= tgt)
    else:                                               # live VWAP as of the previous minute
        vprev = m.vw[e - 1:end]
        th = (hi >= vprev) if d == 1 else (lo <= vprev)
        tgt = None
    si = int(np.argmax(sh)) if sh.any() else None
    ti = int(np.argmax(th)) if th.any() else None
    if si is not None and (ti is None or si <= ti):
        j = e + si
        px = min(stop, m.o[j]) if d == 1 else max(stop, m.o[j])
    elif ti is not None:
        j = e + ti
        px = tgt if tgt is not None else m.vw[j - 1]
        if tgt is None:                                 # gap through VWAP: fill at the open
            px = max(px, m.o[j]) if d == 1 else min(px, m.o[j])
    else:
        j = end
        px = m.c[end]
    gross = d * (px - oe) / risk
    net = d * (px * (1 - d * cost / 2) - oe * (1 + d * cost / 2)) / risk
    return j, net, gross


def run(m: Mkt, setup: str, p: dict, cost_mult: float = 1.0) -> pd.DataFrame:
    rows, busy_until, per_day = [], -1, {}
    for i, d, stop, kind in signals(m, setup, p):
        e = m.b_last[i] + 1                             # next M1 bar after the M5 bar closes
        if e >= len(m.o) or m.m1_day[e] != m.b_day[i] or e <= busy_until:
            continue
        day = m.b_day[i]
        if per_day.get(day, 0) >= 3:
            continue
        risk = abs(m.o[e] - stop)
        if setup in ("A", "B") and not (0.25 * m.ATR[i] <= risk <= 3.0 * m.ATR[i]):
            continue
        r = sim(m, e, d, stop, kind, m.cost * cost_mult)
        if r is None:
            continue
        j, net, gross = r
        busy_until = j
        per_day[day] = per_day.get(day, 0) + 1
        rows.append((m.sym, setup, m.ddate[day], int(m.mod[e]), d, net, gross, risk / m.ATR[i], i))
    return pd.DataFrame(rows, columns=["sym", "setup", "date", "entry_min", "dir", "r", "r_gross", "risk_atr", "m5"])


# ---------------------------------------------------------------------------------------------- metrics
def stats(t: pd.DataFrame) -> dict:
    if len(t) == 0:
        return dict(n=0)
    r = t.r.to_numpy()
    yrs = max((pd.Timestamp(t.date.max()) - pd.Timestamp(t.date.min())).days / 365.25, 0.5)
    eq = np.cumsum(r[np.argsort(t.date.to_numpy(), kind="stable")] * RISK_PCT)
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))
    daily = t.groupby("date").r.sum()
    tstat = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 else np.nan
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(t), per_yr=round(len(t) / yrs), win=round(100 * (r > 0).mean(), 1), exp=round(r.mean(), 3),
                exp_gross=round(t.r_gross.mean(), 3), PF=round(pos / neg, 2) if neg > 0 else np.nan,
                maxDD_pct=round(dd, 1), t=round(float(tstat), 2))


def split(t: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    hold = t.date >= HOLDOUT
    return t[~hold], t[hold]


# ---------------------------------------------------------------------------------------------- random control
def random_control(mk: dict[str, Mkt], setup: str, real: pd.DataFrame, reps: int) -> np.ndarray:
    """Each real trade -> a random entry: same market, direction, period, time window, stop distance in ATR5
    units and exit rule. For C the random entry must be on the same side of VWAP as the fade (short above VWAP)."""
    pools = {}
    for s, m in mk.items():
        last_entry_b = (m.L - 60) // 5 - 1
        bmin = 11 if setup == "C" else 5
        ok = (m.B >= bmin) & (m.B <= last_entry_b) & (m.b_last + 1 < len(m.o))
        hold = np.array([m.ddate[x] >= HOLDOUT for x in m.b_day])
        for per in (False, True):
            base = ok & (hold == per)
            if setup == "C":
                pools[(s, per, 1)] = np.flatnonzero(base & (m.C5 < m.V5))
                pools[(s, per, -1)] = np.flatnonzero(base & (m.C5 > m.V5))
            else:
                idx = np.flatnonzero(base)
                pools[(s, per, 1)] = pools[(s, per, -1)] = idx
    tr = real.assign(per=real.date >= HOLDOUT)[["sym", "per", "dir", "risk_atr"]].to_numpy()
    kind = ("VWAP", None) if setup == "C" else ("R", BASE[setup]["tgt"])
    out = np.empty(reps)
    for rep in range(reps):
        rs = []
        for s, per, d, ka in tr:
            m = mk[s]
            pool = pools[(s, per, d)]
            for _ in range(5):
                i = pool[rng.integers(len(pool))]
                e = m.b_last[i] + 1
                if m.m1_day[e] != m.b_day[i]:
                    continue
                stop = m.o[e] - d * ka * m.ATR[i]
                r = sim(m, e, d, stop, kind, m.cost)
                if r is not None:
                    rs.append(r[1]); break
        out[rep] = np.mean(rs)
    return out


# ---------------------------------------------------------------------------------------------- main
if __name__ == "__main__":
    pd.set_option("display.width", 200)
    t0 = time.time()
    mk = {s: Mkt(s) for s in SESS}
    print("loaded", {s: (len(m.dstart), str(m.ddate[0]), str(m.ddate[-1])) for s, m in mk.items()},
          f"{time.time() - t0:.0f}s", flush=True)
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 200

    allt, summ = {}, []
    for setup in "ABC":
        t = pd.concat([run(m, setup, BASE[setup]) for m in mk.values()], ignore_index=True)
        t2 = pd.concat([run(m, setup, BASE[setup], 2.0) for m in mk.values()], ignore_index=True)
        allt[setup] = t
        t.to_csv(f"out_vwap_trades_{setup}.csv", index=False)
        dev, hold = split(t)
        summ += [dict(setup=setup, part=k, **stats(x)) for k, x in
                 [("full", t), ("dev", dev), ("holdout", hold), ("full_2x_cost", t2)]]
        persym = t.groupby("sym").apply(lambda g: pd.Series(stats(g)), include_groups=False)
        print(f"\n=== Setup {setup} per market (full period, net) ===\n", persym.to_string(), flush=True)
        ls = t.groupby("dir").apply(lambda g: pd.Series(stats(g)), include_groups=False)
        print(" long(1)/short(-1):\n", ls.to_string(), flush=True)
    S = pd.DataFrame(summ)
    print("\n=== SUMMARY ===\n", S.to_string(index=False), flush=True)
    S.to_csv("out_vwap_summary.csv", index=False)

    # plateau
    pl = []
    for setup in "AB":
        for z in (0.1, 0.2, 0.35):
            for tg in (1.5, 2.0, 3.0):
                t = pd.concat([run(m, setup, dict(zone=z, tgt=tg)) for m in mk.values()], ignore_index=True)
                dev, hold = split(t)
                pl.append(dict(setup=setup, zone=z, tgt=tg, n=len(t), exp=round(t.r.mean(), 3),
                               exp_dev=round(dev.r.mean(), 3), exp_hold=round(hold.r.mean(), 3)))
    for bd in (1.5, 2.0, 2.5):
        for fl in (0.25, 0.5, 1.0):
            t = pd.concat([run(m, "C", dict(band=bd, flat=fl)) for m in mk.values()], ignore_index=True)
            dev, hold = split(t)
            pl.append(dict(setup="C", band=bd, flat=fl, n=len(t), exp=round(t.r.mean(), 3),
                           exp_dev=round(dev.r.mean(), 3), exp_hold=round(hold.r.mean(), 3)))
    P = pd.DataFrame(pl)
    print("\n=== PLATEAU (net expectancy, R) ===\n", P.to_string(index=False), flush=True)
    P.to_csv("out_vwap_plateau.csv", index=False)

    # random control
    rc = []
    for setup in "ABC":
        t = allt[setup]
        ts = time.time()
        sims = random_control(mk, setup, t, reps)
        real = t.r.mean()
        p = float((sims >= real).mean())
        rc.append(dict(setup=setup, real_exp=round(real, 3), rand_mean=round(sims.mean(), 3),
                       rand_p95=round(np.percentile(sims, 95), 3), p=round(p, 3), reps=reps))
        print(f"random control {setup}: real {real:+.3f}R vs random mean {sims.mean():+.3f}R "
              f"(95th pct {np.percentile(sims, 95):+.3f}) p={p:.3f}  [{time.time() - ts:.0f}s]", flush=True)
    pd.DataFrame(rc).to_csv("out_vwap_random.csv", index=False)
    print(f"done in {time.time() - t0:.0f}s")
