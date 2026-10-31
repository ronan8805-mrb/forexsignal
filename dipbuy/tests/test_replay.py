"""Replay years of real daily history through engine.run() with a fake MT5 and compare
every trade with the research backtest (research/validate_mr.simulate, zero costs).

Expected, by design, differences:
  * the engine exits at the NEXT open after the exit rule fires (it can only know at the close);
    the backtest exits at that close -> exit date differs by one bar, R differs slightly.
Everything else (signal days, entry days, stop levels, stop hits, no re-entry on a stop-out bar) must match.

    python tests/test_replay.py                 (needs research/data_daily/*.csv)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "research"))
sys.path.insert(0, str(HERE))

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

import engine                            # noqa: E402
from fake_mt5 import FakeMT5             # noqa: E402

DATA = HERE.parent.parent / "research" / "data_daily"
CASES = {"US500": "usa500idxusd", "JP225": "jpnidxjpy", "IWM": "iwmususd", "XLF": "xlfususd"}


def bars_from_csv(name: str) -> list[dict]:
    d = pd.read_csv(DATA / f"{name}.csv")
    return [{"time": int(r.timestamp // 1000), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
             "tick_volume": float(r.n)} for r in d.itertuples()]


def backtest_trades(name: str) -> pd.DataFrame:
    import os
    import validate_mr as v
    cwd = os.getcwd()
    os.chdir(DATA.parent)
    try:
        x = v.load_daily(name)
    finally:
        os.chdir(cwd)
    up = (x["c"] > x["sma"]) & ~np.isnan(x["sma"])
    zero = dict(spread=0, slip=0, spread_bps=0, slip_bps=0, swap_bps=0)
    t = v.simulate(x, up & (x["rsi"] < v.THR), zero)
    return pd.DataFrame(t, columns=["entry_ts", "exit_ts", "r", "r_gross", "days"])


def replay(canon: str, name: str, tmp: Path) -> pd.DataFrame:
    engine.STATE_FILE, engine.JOURNAL = tmp / f"state_{canon}.json", tmp / f"journal_{canon}.csv"
    bars = bars_from_csv(name)
    fake = FakeMT5({canon: bars})
    cfg = {"account": {"dry_run": False, "risk_pct": 0.5, "magic": 1, "max_open_positions": 99,
                       "max_new_per_day": 99, "pause_drawdown_pct": 1e9, "pause_rolling_trades": 10**9, "max_lots": 1e9},
           "execution": {"max_spread_atr_frac": 1.0, "entry_valid_hours": 1e9, "deviation_points": 50},
           "telegram": {"bot_token": "", "chat_id": ""}, "symbols": {}}
    for k in range(1, len(bars)):
        fake.set_index(canon, k)
        engine.run(fake, cfg)              # runs at the open of bar k
        fake.play_bar(canon)               # bar k trades; stops may hit
    return pd.read_csv(engine.JOURNAL), fake


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    tmp = Path(tempfile.mkdtemp())
    all_ok = True
    for canon, name in CASES.items():
        j, fake = replay(canon, name, tmp)
        bt = backtest_trades(name)
        bt_days = set((bt.entry_ts // 1000).tolist())
        eng_days = set(fake.entries)
        both = bt_days & eng_days
        only_bt, only_eng = sorted(bt_days - eng_days), sorted(eng_days - bt_days)
        eng_r = np.array([e[2] for e in fake.exits])
        stops_eng = sum(1 for e in j.itertuples() if e.event == "CLOSED" and str(e.detail).startswith("stop loss"))
        stops_bt = int((bt.r <= -0.999).sum())
        print(f"{canon:6s} trades: backtest {len(bt_days):4d} | engine {len(eng_days):4d} | same entry day {len(both)} "
              f"({100 * len(both) / max(len(bt_days), 1):.1f}%) | only backtest {len(only_bt)} | only engine {len(only_eng)}")
        print(f"       stop-outs: backtest {stops_bt} engine {stops_eng} | avg R (zero cost): backtest {bt.r.mean():+.3f} "
              f"engine {eng_r.mean():+.3f} | win%: backtest {100 * (bt.r > 0).mean():.1f} engine {100 * (eng_r > 0).mean():.1f}")
        for d in only_eng[:3]:
            print("         engine-only entry:", pd.to_datetime(d, unit="s").date())
        for d in only_bt[:3]:
            print("         backtest-only entry:", pd.to_datetime(d, unit="s").date())
        all_ok &= len(both) / max(len(bt_days), 1) > 0.95 and stops_eng == stops_bt
        all_ok &= len(only_eng) <= 1        # at most the final, still-open trade (backtest drops it)
    print("REPLAY", "PASS" if all_ok else "FAIL")
