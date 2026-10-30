"""Signal pipeline: validate -> caps -> filters -> Telegram -> MT5 dispatch.

Runs in a single background worker so the webhook can answer TradingView fast
(TradingView gives webhooks only a few seconds). Every step's outcome is written
to SQLite so nothing is silently lost; on restart, rows still in 'received' are
re-queued.
"""
from __future__ import annotations

import asyncio
import json
import logging

from .ai_filter import LLMFilter, RuleFilter, decide
from .config import Config
from .dispatch import FileDrop, csv_line
from .formatter import AIView, format_signal
from .models import TVSignal, level_errors, signal_age_seconds
from .store import Store
from .telegram import TelegramSender

log = logging.getLogger("bridge.pipeline")


class Pipeline:
    def __init__(self, cfg: Config, store: Store, telegram: TelegramSender | None,
                 llm: LLMFilter | None, file_drop: FileDrop | None):
        self.cfg = cfg
        self.store = store
        self.telegram = telegram
        self.llm = llm
        self.file_drop = file_drop
        self.rules = RuleFilter(cfg.rules)
        self.queue: asyncio.Queue[int] = asyncio.Queue()

    # ---- worker -------------------------------------------------------------
    async def run_forever(self) -> None:
        for seq in self.store.unprocessed():
            await self.queue.put(seq)
        while True:
            seq = await self.queue.get()
            try:
                await self.process(seq)
            except Exception:  # noqa: BLE001 — one bad signal must not kill the worker
                log.exception("pipeline error on seq=%s", seq)
                self.store.update(seq, status="error", reject_reason="internal error (see log)")
            finally:
                self.queue.task_done()

    async def _notify_review(self, text: str) -> None:
        if self.telegram and self.cfg.telegram.enabled and self.cfg.telegram.review_chat_id:
            await self.telegram.send(self.cfg.telegram.review_chat_id, text)

    # ---- one signal ----------------------------------------------------------
    async def process(self, seq: int) -> str:
        row = self.store.get(seq)
        if row is None or row["status"] != "received":
            return "skip"
        payload = json.loads(row["payload"])
        sig = TVSignal.model_validate(payload)
        sig.symbol = row["symbol"]  # canonical, resolved at ingest
        spec = self.cfg.symbols[sig.symbol]

        # 1. Hard validation (already checked at ingest; re-check is cheap).
        errs = level_errors(sig)
        if errs:
            self.store.update(seq, status="rejected", reject_reason="; ".join(errs))
            return "rejected"

        # 2. Staleness — a late signal is a different trade.
        age = signal_age_seconds(sig)
        if age is not None and age > self.cfg.rules.max_age_seconds:
            self.store.update(seq, status="rejected", reject_reason=f"stale ({age:.0f}s)")
            return "rejected"

        # 3. Daily caps (bridge-wide, independent of chart settings).
        if self.store.passed_today() >= self.cfg.rules.max_per_day_total:
            self.store.update(seq, status="rejected", reject_reason="daily cap (total)")
            return "rejected"
        if self.store.passed_today(sig.symbol) >= self.cfg.rules.max_per_day_per_symbol:
            self.store.update(seq, status="rejected", reject_reason=f"daily cap ({sig.symbol})")
            return "rejected"

        # 4. Rules + AI.
        dec = await decide(sig, self.rules, self.llm, self.cfg.ai.mode)
        ai_view = None
        upd = {}
        if dec.ai is not None:
            upd = dict(ai_score=dec.ai.score, ai_verdict=dec.ai.verdict, ai_reason=dec.ai.reason,
                       ai_latency_ms=dec.ai.latency_ms)
            ai_view = AIView(self.cfg.ai.mode, dec.ai.score, dec.ai.verdict, dec.ai.reason)

        if not dec.allow:
            self.store.update(seq, status="vetoed", reject_reason=dec.reason, **upd)
            await self._notify_review(format_signal(sig, spec, ai_view, header=f"VETOED — {dec.reason}"))
            return "vetoed"

        self.store.update(seq, status="passed", **upd)

        # 5. MT5 dispatch first (time-sensitive), then Telegram.
        line = csv_line(seq, sig.signal_id, spec, sig.direction, sig.entry, sig.sl,
                        sig.tp1, sig.tp2, sig.tp3, row["received_at"])
        if self.file_drop:
            try:
                self.file_drop.write(seq, sig.signal_id, line)
            except OSError as e:
                log.error("file drop failed for seq=%s: %s", seq, e)
        if self.file_drop or self.cfg.http_poll_enabled:
            self.store.update(seq, dispatched=1)

        tg_status = "disabled"
        if self.telegram and self.cfg.telegram.enabled and self.cfg.telegram.chat_id:
            tg_status = await self.telegram.send(self.cfg.telegram.chat_id, format_signal(sig, spec, ai_view))
        self.store.update(seq, telegram_status=tg_status)
        return "passed"
