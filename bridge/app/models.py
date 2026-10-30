"""Signal schema + validation. Pure functions, no I/O."""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TVSignal(BaseModel):
    """Payload produced by the Pine Script alert (schema 1)."""

    model_config = ConfigDict(extra="allow")

    signal_id: str = Field(min_length=3, max_length=120)
    symbol: str
    direction: Literal["BUY", "SELL"]
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    timeframe: str = ""
    setup: str = ""
    tv_ticker: str = ""
    strategy: str = "ATR-EW"
    schema_: int = Field(default=1, alias="schema")
    atr: float | None = None
    atr_ratio: float | None = None
    adx: float | None = None
    ema_trend: float | None = None
    risk_atr: float | None = None
    pivots: list[float] = Field(default_factory=list)
    recent_closes: list[float] = Field(default_factory=list)
    bar_time: int | None = None          # ms since epoch (bar open)
    timestamp: str | None = None         # ISO bar close, UTC

    @field_validator("direction", mode="before")
    @classmethod
    def _norm_dir(cls, v: str) -> str:
        v = str(v).strip().upper()
        return {"LONG": "BUY", "SHORT": "SELL"}.get(v, v)

    @field_validator("signal_id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        # Used in file names and MT5 CSV — keep it boring.
        if not all(c.isalnum() or c in "-_.:" for c in v):
            raise ValueError("signal_id contains unsupported characters")
        return v.replace(":", "_")

    @field_validator("entry", "sl", "tp1", "tp2", "tp3")
    @classmethod
    def _finite_positive(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("price must be a finite positive number")
        return v

    # ---- derived -----------------------------------------------------------
    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def r_multiple(self, tp: float) -> float:
        return abs(tp - self.entry) / self.risk if self.risk > 0 else 0.0


def level_errors(s: TVSignal) -> list[str]:
    """Structural sanity of the price ladder. Empty list = OK."""
    errs: list[str] = []
    if s.risk <= 0:
        errs.append("zero risk (entry == sl)")
        return errs
    if s.direction == "BUY":
        if not (s.sl < s.entry < s.tp1 <= s.tp2 <= s.tp3):
            errs.append("BUY requires sl < entry < tp1 <= tp2 <= tp3")
    else:
        if not (s.sl > s.entry > s.tp1 >= s.tp2 >= s.tp3):
            errs.append("SELL requires sl > entry > tp1 >= tp2 >= tp3")
    # Guard against absurd payloads (e.g. a mis-typed decimal place).
    if s.risk / s.entry > 0.05:
        errs.append("stop distance > 5% of price — refusing")
    return errs


def signal_age_seconds(s: TVSignal, now: float | None = None) -> float | None:
    """Age relative to bar close. bar_time is bar OPEN (ms); we only know close via timestamp."""
    now = now or time.time()
    if s.timestamp:
        try:
            ts = datetime.strptime(s.timestamp, "%Y-%m-%dT%H:%M:%SZ")
            return now - ts.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    if s.bar_time:
        return now - s.bar_time / 1000.0
    return None
