"""Configuration: YAML file for structure, environment variables for secrets.

Secrets (webhook secret, bridge token, Telegram token) are NEVER stored in the
YAML. The YAML names the env var to read instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SymbolSpec:
    canonical: str
    aliases: list[str]
    pip: float
    digits: int


@dataclass
class TelegramCfg:
    enabled: bool = True
    bot_token: str = ""
    chat_id: str = ""
    review_chat_id: str = ""          # optional: vetoed/rejected signals go here for human review
    max_retries: int = 5
    timeout_seconds: float = 10.0
    api_base: str = "https://api.telegram.org"


@dataclass
class FileDropCfg:
    enabled: bool = True
    dir: str = "./mt5_inbox"
    retention_hours: int = 48


@dataclass
class AICfg:
    mode: str = "shadow"              # off | shadow | veto
    backend: str = "ollama"           # ollama | openai_compat (llama.cpp server, vLLM, LM Studio)
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b-instruct-q4_K_M"
    timeout_seconds: float = 20.0
    min_score: int = 55               # veto below this score (only in veto mode)
    temperature: float = 0.0


@dataclass
class RulesCfg:
    max_age_seconds: int = 1200
    max_per_day_total: int = 6
    max_per_day_per_symbol: int = 3
    min_rr_tp2: float = 1.5
    max_risk_atr: float = 3.5


@dataclass
class Config:
    webhook_secret: str
    bridge_token: str
    symbols: dict[str, SymbolSpec]
    telegram: TelegramCfg = field(default_factory=TelegramCfg)
    file_drop: FileDropCfg = field(default_factory=FileDropCfg)
    http_poll_enabled: bool = True
    ai: AICfg = field(default_factory=AICfg)
    rules: RulesCfg = field(default_factory=RulesCfg)
    sqlite_path: str = "data/signals.db"
    allowed_ips: list[str] = field(default_factory=list)
    trust_forwarded_for: bool = False

    # alias (upper-cased) -> canonical symbol
    def resolve_symbol(self, raw: str) -> str | None:
        key = raw.strip().upper()
        for spec in self.symbols.values():
            if key == spec.canonical or key in spec.aliases:
                return spec.canonical
        # Fall back to the part after an exchange prefix, e.g. "OANDA:EURUSD"
        if ":" in key:
            return self.resolve_symbol(key.split(":", 1)[1])
        return None


def _env(name: str | None, default: str = "") -> str:
    if not name:
        return default
    return os.environ.get(name, default)


def _sub(d: dict[str, Any], key: str) -> dict[str, Any]:
    v = d.get(key) or {}
    if not isinstance(v, dict):
        raise ValueError(f"config section '{key}' must be a mapping")
    return v


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    sec = _sub(raw, "security")
    webhook_secret = _env(sec.get("webhook_secret_env", "WEBHOOK_SECRET"))
    bridge_token = _env(sec.get("bridge_token_env", "BRIDGE_TOKEN"))
    if not webhook_secret:
        raise ValueError("Webhook secret is empty. Set the env var named in security.webhook_secret_env.")

    symbols: dict[str, SymbolSpec] = {}
    for canon, spec in (raw.get("symbols") or {}).items():
        c = canon.upper()
        aliases = [a.upper() for a in spec.get("aliases", [])]
        symbols[c] = SymbolSpec(canonical=c, aliases=aliases, pip=float(spec["pip"]), digits=int(spec["digits"]))
    if not symbols:
        raise ValueError("No symbols configured.")

    tg = _sub(raw, "telegram")
    telegram = TelegramCfg(
        enabled=bool(tg.get("enabled", True)),
        bot_token=_env(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")),
        chat_id=_env(tg.get("chat_id_env", "TELEGRAM_CHAT_ID")),
        review_chat_id=_env(tg.get("review_chat_id_env", "TELEGRAM_REVIEW_CHAT_ID")),
        max_retries=int(tg.get("max_retries", 5)),
        timeout_seconds=float(tg.get("timeout_seconds", 10)),
        api_base=str(tg.get("api_base", "https://api.telegram.org")),
    )

    mt5 = _sub(raw, "mt5")
    fd = mt5.get("file_drop") or {}
    file_drop = FileDropCfg(
        enabled=bool(fd.get("enabled", True)),
        dir=os.path.expandvars(str(fd.get("dir", "./mt5_inbox"))),
        retention_hours=int(fd.get("retention_hours", 48)),
    )
    http_poll_enabled = bool((mt5.get("http_poll") or {}).get("enabled", True))

    a = _sub(raw, "ai")
    ai = AICfg(
        mode=str(a.get("mode", "shadow")).lower(),
        backend=str(a.get("backend", "ollama")).lower(),
        base_url=str(a.get("base_url", "http://127.0.0.1:11434")).rstrip("/"),
        model=str(a.get("model", "qwen2.5:7b-instruct-q4_K_M")),
        timeout_seconds=float(a.get("timeout_seconds", 20)),
        min_score=int(a.get("min_score", 55)),
        temperature=float(a.get("temperature", 0.0)),
    )
    if ai.mode not in {"off", "shadow", "veto"}:
        raise ValueError("ai.mode must be off | shadow | veto")
    if ai.backend not in {"ollama", "openai_compat"}:
        raise ValueError("ai.backend must be ollama | openai_compat")

    r = _sub(raw, "rules")
    rules = RulesCfg(
        max_age_seconds=int(r.get("max_age_seconds", 1200)),
        max_per_day_total=int(r.get("max_per_day_total", 6)),
        max_per_day_per_symbol=int(r.get("max_per_day_per_symbol", 3)),
        min_rr_tp2=float(r.get("min_rr_tp2", 1.5)),
        max_risk_atr=float(r.get("max_risk_atr", 3.5)),
    )

    net = _sub(raw, "network")
    return Config(
        webhook_secret=webhook_secret,
        bridge_token=bridge_token,
        symbols=symbols,
        telegram=telegram,
        file_drop=file_drop,
        http_poll_enabled=http_poll_enabled,
        ai=ai,
        rules=rules,
        sqlite_path=str(_sub(raw, "storage").get("sqlite_path", "data/signals.db")),
        allowed_ips=list(net.get("allowed_ips", []) or []),
        trust_forwarded_for=bool(net.get("trust_forwarded_for", False)),
    )
