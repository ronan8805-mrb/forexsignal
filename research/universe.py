"""Instrument universe and cost assumptions (documented, conservative retail ECN/CFD levels).

spread / slip are absolute price units; *_bps are basis points of price (used where the
price level changed a lot over 20 years: crypto and indices). swap_bps is charged per
overnight rollover (22:00 UTC) regardless of direction (conservative: real swaps are
sometimes positive for one side).
"""
from __future__ import annotations

UNIVERSE = {
    #  symbol         class       spread    slip      spread_bps slip_bps swap_bps  first_full_year
    "eurusd":        ("fx",       0.00010,  0.00002,  0,         0,       0.3,      2007),
    "gbpusd":        ("fx",       0.00014,  0.00003,  0,         0,       0.3,      2007),
    "audusd":        ("fx",       0.00012,  0.00002,  0,         0,       0.3,      2007),
    "nzdusd":        ("fx",       0.00018,  0.00003,  0,         0,       0.3,      2007),
    "usdcad":        ("fx",       0.00016,  0.00003,  0,         0,       0.3,      2007),
    "usdchf":        ("fx",       0.00015,  0.00003,  0,         0,       0.3,      2007),
    "usdjpy":        ("fx",       0.012,    0.003,    0,         0,       0.3,      2007),
    "xauusd":        ("metal",    0.30,     0.05,     0,         0,       1.5,      2007),
    "xagusd":        ("metal",    0.025,    0.005,    0,         0,       1.5,      2007),
    "usa500idxusd":  ("index",    0.0,      0.0,      1.5,       0.5,     2.0,      2012),
    "usatechidxusd": ("index",    0.0,      0.0,      1.5,       0.5,     2.0,      2012),
    "deuidxeur":     ("index",    0.0,      0.0,      1.5,       0.5,     2.0,      2014),
    "btcusd":        ("crypto",   0.0,      0.0,      5.0,       2.0,     5.5,      2018),
    "ethusd":        ("crypto",   0.0,      0.0,      8.0,       3.0,     5.5,      2018),
}


def cost_kwargs(sym: str, mult: float = 1.0) -> dict:
    _, spread, slip, sbps, slbps, swap, _ = UNIVERSE[sym]
    return dict(spread=spread * mult, slip=slip * mult, spread_bps=sbps * mult,
                slip_bps=slbps * mult, swap_bps=swap * mult)


def data_path(sym: str) -> str:
    return f"data/{sym}/h1.csv"
