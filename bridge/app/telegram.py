"""Telegram Bot API sender with bounded retries.

Retry policy (boring on purpose):
  - network errors / 5xx  -> exponential backoff 1s, 2s, 4s, 8s ... (capped at 30s)
  - 429 Too Many Requests -> wait parameters.retry_after seconds
  - other 4xx             -> do NOT retry (bad token, bad chat id, bad HTML) — log and give up
"""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("bridge.telegram")


class TelegramSender:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org", max_retries: int = 5,
                 timeout: float = 10.0, client: httpx.AsyncClient | None = None, base_delay: float = 1.0):
        self._url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"
        self._max_retries = max(1, max_retries)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._base_delay = base_delay

    async def send(self, chat_id: str, text: str) -> str:
        """Returns 'sent', 'failed:<reason>'. Never raises."""
        body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        delay = self._base_delay
        last = "unknown"
        for attempt in range(1, self._max_retries + 1):
            try:
                r = await self._client.post(self._url, json=body)
                if r.status_code == 200 and r.json().get("ok"):
                    return "sent"
                if r.status_code == 429:
                    retry_after = float((r.json().get("parameters") or {}).get("retry_after", delay))
                    last = f"429 retry_after={retry_after}"
                    log.warning("telegram rate-limited, attempt %d, waiting %.1fs", attempt, retry_after)
                    await asyncio.sleep(min(retry_after, 60))
                    continue
                if 400 <= r.status_code < 500:
                    try:
                        desc = r.json().get("description", "")
                    except ValueError:
                        desc = r.text[:200]
                    log.error("telegram rejected message (%s): %s", r.status_code, desc)
                    return f"failed:{r.status_code} {desc}"[:200]
                last = f"http {r.status_code}"
            except (httpx.HTTPError, ValueError) as e:
                last = f"{type(e).__name__}: {e}"
            log.warning("telegram send attempt %d/%d failed: %s", attempt, self._max_retries, last)
            if attempt < self._max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        return f"failed:{last}"[:200]

    async def aclose(self) -> None:
        await self._client.aclose()
