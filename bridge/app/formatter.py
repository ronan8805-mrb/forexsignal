"""Telegram message formatting.

Each message has two parts:
  1. Human-readable block (HTML parse mode).
  2. One machine-parseable line inside <code>, e.g.
     #SIG v1 id=EURUSD-15-1730000000000 sym=EURUSD dir=BUY entry=1.08500 sl=1.08350 tp1=... tp2=... tp3=... ts=...
     parse_machine_line() is the inverse — use it in any downstream copier bot.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .config import SymbolSpec
from .models import TVSignal

MACHINE_PREFIX = "#SIG v1"
_KV = re.compile(r"(\w+)=(\S+)")


@dataclass
class AIView:
    mode: str              # off | shadow | veto
    score: float | None
    verdict: str | None    # pass | veto | unavailable
    reason: str | None


def _fmt(x: float, digits: int) -> str:
    return f"{x:.{digits}f}"


def machine_line(s: TVSignal, spec: SymbolSpec) -> str:
    d = spec.digits
    parts = [
        MACHINE_PREFIX,
        f"id={s.signal_id}",
        f"sym={spec.canonical}",
        f"dir={s.direction}",
        f"entry={_fmt(s.entry, d)}",
        f"sl={_fmt(s.sl, d)}",
        f"tp1={_fmt(s.tp1, d)}",
        f"tp2={_fmt(s.tp2, d)}",
        f"tp3={_fmt(s.tp3, d)}",
        f"tf={s.timeframe or 'NA'}",
        f"ts={s.timestamp or 'NA'}",
    ]
    return " ".join(parts)


def parse_machine_line(text: str) -> dict[str, str] | None:
    """Find and parse the #SIG line anywhere in a message. Returns None if absent."""
    for line in text.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line.startswith(MACHINE_PREFIX):
            return dict(_KV.findall(line[len(MACHINE_PREFIX):]))
    return None


def format_signal(s: TVSignal, spec: SymbolSpec, ai: AIView | None = None, header: str = "") -> str:
    d = spec.digits
    pips = lambda a, b: abs(a - b) / spec.pip  # noqa: E731
    arrow = "🟢 BUY" if s.direction == "BUY" else "🔴 SELL"
    tf = f"M{s.timeframe}" if s.timeframe.isdigit() and int(s.timeframe) < 60 else (
        f"H{int(s.timeframe) // 60}" if s.timeframe.isdigit() else s.timeframe
    )
    lines = []
    if header:
        lines.append(f"<b>{html.escape(header)}</b>")
    lines += [
        f"<b>{arrow} {spec.canonical}</b>  <i>{html.escape(tf)} · {html.escape(s.setup or '-')}</i>",
        "",
        f"<b>Entry</b> <code>{_fmt(s.entry, d)}</code>",
        f"<b>SL</b>    <code>{_fmt(s.sl, d)}</code>  ({pips(s.entry, s.sl):.1f} pips)",
        f"<b>TP1</b>   <code>{_fmt(s.tp1, d)}</code>  ({s.r_multiple(s.tp1):.1f}R)",
        f"<b>TP2</b>   <code>{_fmt(s.tp2, d)}</code>  ({s.r_multiple(s.tp2):.1f}R)",
        f"<b>TP3</b>   <code>{_fmt(s.tp3, d)}</code>  ({s.r_multiple(s.tp3):.1f}R)",
    ]
    ctx = []
    if s.atr is not None:
        ctx.append(f"ATR {s.atr:.{d}f}")
    if s.atr_ratio is not None:
        ctx.append(f"ATRx {s.atr_ratio:.2f}")
    if s.adx is not None:
        ctx.append(f"ADX {s.adx:.0f}")
    if ctx:
        lines += ["", "<i>" + " · ".join(ctx) + "</i>"]
    if ai and ai.mode != "off":
        if ai.verdict == "unavailable":
            lines.append("<i>AI filter: unavailable (passed through)</i>")
        else:
            tag = " (shadow)" if ai.mode == "shadow" else ""
            score = f"{ai.score:.0f}/100" if ai.score is not None else "-"
            reason = html.escape((ai.reason or "")[:160])
            lines.append(f"<i>AI{tag}: {score} {str(ai.verdict).upper()} — {reason}</i>")
    lines += ["", f"<code>{html.escape(machine_line(s, spec))}</code>"]
    return "\n".join(lines)
