"""Pure strategy logic for the daily RSI(2) dip-buy. No MetaTrader, no I/O, stdlib only.

Validated rules (research/REPORT.md sections 9-10), long only, on COMPLETED daily bars:
  entry : close > SMA(200) and RSI(2) < 10            -> buy at the next session open
  stop  : entry - 3 x ATR(14)                          (server-side stop loss)
  exit  : RSI(2) > 70 on a completed daily close, or 10 daily bars held -> sell at next open
Indicator definitions match research/validate_mr.py exactly:
  RSI(2): Wilder smoothing (EMA alpha = 1/2) of up/down moves, 50 when undefined
  ATR(14): EMA alpha = 1/14 of true range
"""
from __future__ import annotations

from dataclasses import dataclass

THRESHOLD = 10.0
EXIT_RSI = 70.0
SMA_LEN = 200
ATR_LEN = 14
STOP_ATR = 3.0
MAX_BARS = 10
MIN_BARS = SMA_LEN + 2        # history needed before the first decision (backtest starts at bar index 201)


def rsi2(closes: list[float]) -> list[float]:
    out = [50.0] * len(closes)
    up = dn = None
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        u, w = max(d, 0.0), max(-d, 0.0)
        up = u if up is None else 0.5 * u + 0.5 * up
        dn = w if dn is None else 0.5 * w + 0.5 * dn
        if dn == 0:
            out[i] = 50.0      # same convention as the backtest (division undefined -> 50)
        else:
            out[i] = 100.0 - 100.0 / (1.0 + up / dn)
    return out


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    s = 0.0
    for i, x in enumerate(values):
        s += x
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = ATR_LEN) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    a = None
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        tr = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
        a = tr if a is None else (1.0 / n) * tr + (1.0 - 1.0 / n) * a
        out[i] = a
    return out


@dataclass
class Snapshot:
    """Indicator values on the last COMPLETED daily bar."""
    bar_time: int          # unix seconds of that bar's open
    close: float
    sma200: float
    rsi: float
    atr: float

    @property
    def uptrend(self) -> bool:
        return self.close > self.sma200

    @property
    def entry_signal(self) -> bool:
        return self.uptrend and self.rsi < THRESHOLD


def snapshot(bars: list[dict]) -> Snapshot | None:
    """bars: completed daily bars oldest -> newest, each {time, open, high, low, close}."""
    if len(bars) < MIN_BARS:
        return None
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    s = sma(c, SMA_LEN)[-1]
    a = atr(h, l, c)[-1]
    if s is None or a is None or a <= 0:
        return None
    return Snapshot(bar_time=int(bars[-1]["time"]), close=c[-1], sma200=s, rsi=rsi2(c)[-1], atr=a)


def exit_due(snap: Snapshot, bars_held: int) -> str | None:
    """bars_held = completed daily bars since (and including) the entry bar."""
    if snap.rsi > EXIT_RSI:
        return f"RSI(2) {snap.rsi:.1f} > {EXIT_RSI:.0f}"
    if bars_held >= MAX_BARS:
        return f"time stop ({bars_held} days)"
    return None


def stop_price(entry_price: float, atr_value: float) -> float:
    return entry_price - STOP_ATR * atr_value
