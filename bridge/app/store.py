"""SQLite persistence. Every signal and every execution report is kept —
this table IS the measurement infrastructure (see tools/report.py)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id      TEXT NOT NULL UNIQUE,
    received_at    REAL NOT NULL,
    symbol         TEXT NOT NULL,
    direction      TEXT NOT NULL,
    setup          TEXT,
    timeframe      TEXT,
    entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
    payload        TEXT NOT NULL,
    status         TEXT NOT NULL,      -- received | passed | vetoed | rejected | error
    reject_reason  TEXT,
    ai_score       REAL,
    ai_verdict     TEXT,
    ai_reason      TEXT,
    ai_latency_ms  INTEGER,
    telegram_status TEXT,
    dispatched     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS ix_signals_received ON signals(received_at);

CREATE TABLE IF NOT EXISTS executions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  REAL NOT NULL,
    account      TEXT,
    signal_id    TEXT,
    event        TEXT NOT NULL,        -- OPEN | PARTIAL | CLOSE | SKIP | ERROR ...
    symbol       TEXT,
    ticket       TEXT,
    volume       REAL,
    price        REAL,
    profit       REAL,
    r_multiple   REAL,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_exec_signal ON executions(signal_id);
"""


def utc_day_start(now: float | None = None) -> float:
    d = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- signals -----------------------------------------------------------
    def insert_signal(self, sig: dict[str, Any], payload: dict[str, Any]) -> int | None:
        """Returns seq, or None if this signal_id was already received (dedup)."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    """INSERT INTO signals (signal_id, received_at, symbol, direction, setup, timeframe,
                       entry, sl, tp1, tp2, tp3, payload, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'received')""",
                    (
                        sig["signal_id"], time.time(), sig["symbol"], sig["direction"], sig.get("setup"),
                        sig.get("timeframe"), sig["entry"], sig["sl"], sig["tp1"], sig["tp2"], sig["tp3"],
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def get(self, seq: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM signals WHERE seq=?", (seq,)).fetchone()

    def update(self, seq: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE signals SET {cols} WHERE seq=?", (*fields.values(), seq))

    def unprocessed(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute("SELECT seq FROM signals WHERE status='received' ORDER BY seq").fetchall()
        return [int(r["seq"]) for r in rows]

    def passed_today(self, symbol: str | None = None, now: float | None = None) -> int:
        q = "SELECT COUNT(*) FROM signals WHERE status='passed' AND received_at >= ?"
        args: list[Any] = [utc_day_start(now)]
        if symbol:
            q += " AND symbol=?"
            args.append(symbol)
        with self._lock:
            return int(self._conn.execute(q, args).fetchone()[0])

    def max_seq(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM signals").fetchone()[0])

    def dispatched_after(self, after_seq: int, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM signals WHERE dispatched=1 AND seq > ? ORDER BY seq LIMIT ?",
                (after_seq, limit),
            ).fetchall()

    # ---- executions --------------------------------------------------------
    def insert_execution(self, e: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO executions (received_at, account, signal_id, event, symbol, ticket,
                   volume, price, profit, r_multiple, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(), e.get("account"), e.get("signal_id"), e["event"], e.get("symbol"),
                    str(e.get("ticket") or ""), e.get("volume"), e.get("price"), e.get("profit"),
                    e.get("r_multiple"), e.get("detail"),
                ),
            )
            return int(cur.lastrowid)

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()
