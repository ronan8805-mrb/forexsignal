"""MT5 dispatch.

Wire format (one line, ASCII, comma-separated — trivial to parse in MQL5):

    seq,signal_id,symbol,direction,entry,sl,tp1,tp2,tp3,received_unix

Two transports, both serve the same line:
  * File drop  — bridge writes <inbox>/<seq:09d>_<signal_id>.sig atomically
                 (write .tmp, then os.replace). Point inbox at
                 %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\ATR_EW\\inbox
                 so every terminal on the machine sees it. Files are NOT deleted by
                 the EA (several terminals may read them); the bridge prunes them
                 after retention_hours.
  * HTTP poll  — EA calls GET /api/v1/signals/pending?after=<seq>. Needs the bridge on
                 port 80/443 because MT5 WebRequest only uses those ports.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .config import SymbolSpec

log = logging.getLogger("bridge.dispatch")

CSV_HEADER = "seq,signal_id,symbol,direction,entry,sl,tp1,tp2,tp3,received_unix"


def csv_line(seq: int, signal_id: str, spec: SymbolSpec, direction: str, entry: float, sl: float,
             tp1: float, tp2: float, tp3: float, received_unix: float) -> str:
    d = spec.digits
    return ",".join([
        str(seq), signal_id, spec.canonical, direction,
        f"{entry:.{d}f}", f"{sl:.{d}f}", f"{tp1:.{d}f}", f"{tp2:.{d}f}", f"{tp3:.{d}f}",
        str(int(received_unix)),
    ])


class FileDrop:
    def __init__(self, inbox: str, retention_hours: int = 48):
        self.inbox = Path(inbox)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.retention_s = retention_hours * 3600

    def write(self, seq: int, signal_id: str, line: str) -> Path:
        final = self.inbox / f"{seq:09d}_{signal_id}.sig"
        tmp = final.with_suffix(".tmp")
        tmp.write_text(line + "\n", encoding="ascii")
        os.replace(tmp, final)  # atomic on the same volume: EA never sees a half-written file
        return final

    def prune(self) -> int:
        cutoff = time.time() - self.retention_s
        n = 0
        for p in self.inbox.glob("*.sig"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except OSError as e:
                log.warning("prune failed for %s: %s", p, e)
        return n
