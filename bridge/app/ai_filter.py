"""Signal filter layer.

Two stages, both return a FilterResult:

  RuleFilter  — deterministic checks (R:R, stop width in ATR). Always on.
  LLMFilter   — local quantized model (Ollama or any OpenAI-compatible server such
                as llama.cpp `llama-server`). Scores 0-100 against recent price
                action, ATR context and wave structure.

FAIL-OPEN CONTRACT: any LLM error (timeout, connection refused, bad JSON) returns
verdict="unavailable" and the signal PASSES. The core pipeline never blocks on AI.

Modes (config ai.mode):
  off     — LLM not called.
  shadow  — LLM called, score logged + shown, never vetoes.  <- start here
  veto    — score < min_score vetoes the signal.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import AICfg, RulesCfg
from .models import TVSignal

log = logging.getLogger("bridge.ai")


@dataclass
class FilterResult:
    verdict: str                 # pass | veto | unavailable
    score: float | None = None
    reason: str = ""
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Deterministic rules
# ---------------------------------------------------------------------------
class RuleFilter:
    def __init__(self, rules: RulesCfg):
        self.rules = rules

    def evaluate(self, s: TVSignal) -> FilterResult:
        rr2 = s.r_multiple(s.tp2)
        if rr2 < self.rules.min_rr_tp2:
            return FilterResult("veto", reason=f"TP2 R:R {rr2:.2f} < {self.rules.min_rr_tp2}")
        if s.atr and s.atr > 0:
            risk_atr = s.risk / s.atr
            if risk_atr > self.rules.max_risk_atr:
                return FilterResult("veto", reason=f"stop {risk_atr:.2f} ATR > {self.rules.max_risk_atr}")
        return FilterResult("pass", reason="rules ok")


# ---------------------------------------------------------------------------
# LLM scorer
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a strict risk reviewer for an automated forex signal system.
You receive one candidate trade produced by a rules engine (ATR volatility filter + Elliott Wave swing structure).
Judge ONLY from the data given. Do not invent news or fundamentals.

Score 0-100 how well the trade is supported by the data:
- Is the move into entry already overextended relative to ATR (late chase)? Lower score.
- Do recent closes show momentum in the trade direction after the wave-2 / C pivot? Higher score.
- Is the wave structure clean (clear swings, sensible retracement ratios)? Higher score.
- Is the stop inside recent noise (tiny vs ATR) or is the target ladder unrealistic vs recent range? Lower score.
- Is volatility regime (atr_ratio, adx) supportive? atr_ratio ~0.9-1.8 and adx > 20 is supportive.

Respond with JSON only: {"score": <int 0-100>, "reason": "<max 25 words>"}"""


def build_user_prompt(s: TVSignal) -> str:
    atr = s.atr or 0.0
    closes = s.recent_closes[-30:]
    # Normalise recent closes as ATR-multiples of change vs entry: scale-free for the model.
    norm = [round((c - s.entry) / atr, 2) for c in closes] if atr > 0 else closes
    pivots_atr = [round((p - s.entry) / atr, 2) for p in s.pivots] if atr > 0 else s.pivots
    ctx = {
        "symbol": s.symbol,
        "timeframe": s.timeframe,
        "direction": s.direction,
        "setup": s.setup,
        "setup_meaning": {
            "W3": "pivots = [wave0, wave1, wave2]; entering wave 3",
            "ABC": "pivots = [correction start, A, B, C]; entering trend resumption after C",
        }.get(s.setup, "unknown"),
        "entry": s.entry,
        "sl_R": 1.0,
        "risk_in_atr": round(s.risk / atr, 2) if atr > 0 else None,
        "tp_R_multiples": [round(s.r_multiple(t), 2) for t in (s.tp1, s.tp2, s.tp3)],
        "atr_ratio": s.atr_ratio,
        "adx": s.adx,
        "price_vs_trend_ema_atr": round((s.entry - s.ema_trend) / atr, 2) if (atr > 0 and s.ema_trend) else None,
        "pivots_relative_to_entry_in_atr": pivots_atr,
        "recent_closes_relative_to_entry_in_atr_oldest_first": norm,
    }
    return "Candidate trade:\n" + json.dumps(ctx, separators=(",", ":"))


_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def parse_llm_json(text: str) -> tuple[float, str]:
    """Extract {"score":..,"reason":..}. Tolerates <think> blocks / code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = _JSON_OBJ.search(text)
    if not m:
        raise ValueError("no JSON object in model output")
    obj = json.loads(m.group(0))
    score = float(obj["score"])
    if not 0 <= score <= 100:
        raise ValueError(f"score out of range: {score}")
    return score, str(obj.get("reason", ""))[:300]


class LLMFilter:
    def __init__(self, cfg: AICfg, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds)

    async def _call(self, user_prompt: str) -> str:
        if self.cfg.backend == "ollama":
            r = await self._client.post(
                f"{self.cfg.base_url}/api/chat",
                json={
                    "model": self.cfg.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": self.cfg.temperature, "num_predict": 200},
                },
                timeout=self.cfg.timeout_seconds,
            )
            r.raise_for_status()
            return r.json()["message"]["content"]
        # OpenAI-compatible (llama.cpp llama-server, vLLM, LM Studio)
        r = await self._client.post(
            f"{self.cfg.base_url}/v1/chat/completions",
            json={
                "model": self.cfg.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.cfg.temperature,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            timeout=self.cfg.timeout_seconds,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def evaluate(self, s: TVSignal) -> FilterResult:
        t0 = time.monotonic()
        try:
            raw = await self._call(build_user_prompt(s))
            score, reason = parse_llm_json(raw)
        except Exception as e:  # noqa: BLE001 — fail-open is the contract
            ms = int((time.monotonic() - t0) * 1000)
            log.warning("AI filter unavailable (%s: %s) — passing signal through", type(e).__name__, e)
            return FilterResult("unavailable", None, f"{type(e).__name__}", ms)
        ms = int((time.monotonic() - t0) * 1000)
        verdict = "pass" if score >= self.cfg.min_score else "veto"
        return FilterResult(verdict, score, reason, ms)

    async def health(self) -> bool:
        try:
            path = "/api/tags" if self.cfg.backend == "ollama" else "/v1/models"
            r = await self._client.get(f"{self.cfg.base_url}{path}", timeout=3)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class Decision:
    allow: bool
    rule: FilterResult
    ai: FilterResult | None
    reason: str


async def decide(s: TVSignal, rules: RuleFilter, llm: LLMFilter | None, mode: str) -> Decision:
    r = rules.evaluate(s)
    if r.verdict == "veto":
        return Decision(False, r, None, f"rule: {r.reason}")
    if mode == "off" or llm is None:
        return Decision(True, r, None, "rules ok")
    ai = await llm.evaluate(s)
    if mode == "veto" and ai.verdict == "veto":
        return Decision(False, r, ai, f"ai: {ai.score:.0f} < min — {ai.reason}")
    return Decision(True, r, ai, "passed")
