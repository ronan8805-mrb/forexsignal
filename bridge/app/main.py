"""FastAPI app: TradingView webhook in, Telegram + MT5 out.

Endpoints
  POST /webhook/tradingview        TradingView alert body (JSON built by the Pine script)
  GET  /api/v1/signals/pending     MT5 EA HTTP poll   (header X-Bridge-Token)
  POST /api/v1/executions          MT5 EA trade reports (header X-Bridge-Token)
  GET  /health                     liveness + AI backend reachability

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
      (config path from env BRIDGE_CONFIG, default ./config.yaml)
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError

from .ai_filter import LLMFilter
from .config import Config, load_config
from .dispatch import CSV_HEADER, FileDrop, csv_line
from .models import TVSignal, level_errors
from .pipeline import Pipeline
from .store import Store
from .telegram import TelegramSender

log = logging.getLogger("bridge")


class ExecutionReport(BaseModel):
    account: str | None = None
    seq: int | None = None            # EA knows the bridge seq; we resolve signal_id from it
    signal_id: str | None = None
    event: str
    symbol: str | None = None
    ticket: str | int | None = None
    volume: float | None = None
    price: float | None = None
    profit: float | None = None
    r_multiple: float | None = None
    detail: str | None = None


def _client_ip(request: Request, trust_forwarded: bool) -> str:
    if trust_forwarded:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def create_app(cfg: Config, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`transport` lets tests inject a mock HTTP layer for Telegram + LLM."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(cfg.sqlite_path)
        tg = None
        if cfg.telegram.enabled and cfg.telegram.bot_token:
            tg = TelegramSender(cfg.telegram.bot_token, cfg.telegram.api_base, cfg.telegram.max_retries,
                                client=httpx.AsyncClient(timeout=cfg.telegram.timeout_seconds, transport=transport),
                                base_delay=0.01 if transport else 1.0)
        llm = None
        if cfg.ai.mode != "off":
            llm = LLMFilter(cfg.ai, client=httpx.AsyncClient(timeout=cfg.ai.timeout_seconds, transport=transport))
        fd = FileDrop(cfg.file_drop.dir, cfg.file_drop.retention_hours) if cfg.file_drop.enabled else None
        pipe = Pipeline(cfg, store, tg, llm, fd)

        app.state.store, app.state.pipeline, app.state.llm = store, pipe, llm
        worker = asyncio.create_task(pipe.run_forever(), name="pipeline")

        async def housekeeping():
            while True:
                if fd:
                    with contextlib.suppress(Exception):
                        fd.prune()
                await asyncio.sleep(3600)

        hk = asyncio.create_task(housekeeping(), name="housekeeping")
        if llm:
            ok = await llm.health()
            log.info("AI backend %s (%s) reachable=%s mode=%s", cfg.ai.backend, cfg.ai.model, ok, cfg.ai.mode)
        try:
            yield
        finally:
            for t in (worker, hk):
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            if tg:
                await tg.aclose()
            if llm:
                await llm.aclose()
            store.close()

    app = FastAPI(title="ATR-EW signal bridge", version="1.0", lifespan=lifespan)

    def _check_token(token: str | None) -> None:
        if not cfg.bridge_token or not token or not hmac.compare_digest(token, cfg.bridge_token):
            raise HTTPException(status_code=401, detail="bad token")

    @app.post("/webhook/tradingview")
    async def webhook(request: Request):
        if cfg.allowed_ips:
            ip = _client_ip(request, cfg.trust_forwarded_for)
            if ip not in cfg.allowed_ips:
                log.warning("webhook from non-allowed IP %s", ip)
                raise HTTPException(status_code=403, detail="forbidden")

        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        secret = str(payload.pop("secret", ""))
        if not hmac.compare_digest(secret, cfg.webhook_secret):
            raise HTTPException(status_code=401, detail="bad secret")

        try:
            sig = TVSignal.model_validate(payload)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=json.loads(e.json(include_url=False)))

        canonical = cfg.resolve_symbol(sig.symbol) or (cfg.resolve_symbol(sig.tv_ticker) if sig.tv_ticker else None)
        if not canonical:
            raise HTTPException(status_code=422, detail=f"unknown symbol '{sig.symbol}' (add it to config symbols)")
        sig.symbol = canonical

        errs = level_errors(sig)
        if errs:
            raise HTTPException(status_code=422, detail=errs)

        store: Store = request.app.state.store
        seq = store.insert_signal(sig.model_dump(by_alias=True), payload)
        if seq is None:
            # TradingView may retry; duplicates are acknowledged and ignored.
            return {"status": "duplicate", "signal_id": sig.signal_id}
        await request.app.state.pipeline.queue.put(seq)
        return {"status": "accepted", "seq": seq, "signal_id": sig.signal_id}

    @app.get("/api/v1/signals/pending", response_class=PlainTextResponse)
    async def pending(request: Request, after: int = 0, limit: int = 50,
                      x_bridge_token: str | None = Header(default=None)):
        if not cfg.http_poll_enabled:
            raise HTTPException(status_code=404, detail="http poll disabled")
        _check_token(x_bridge_token)
        store = request.app.state.store
        rows = store.dispatched_after(after, min(max(limit, 1), 200))
        # First line lets the EA detect a reset DB (its cursor would be ahead of latest_seq).
        lines = [f"# latest_seq={store.max_seq()}", CSV_HEADER]
        for r in rows:
            lines.append(csv_line(r["seq"], r["signal_id"], cfg.symbols[r["symbol"]], r["direction"],
                                  r["entry"], r["sl"], r["tp1"], r["tp2"], r["tp3"], r["received_at"]))
        return "\n".join(lines) + "\n"

    @app.post("/api/v1/executions")
    async def executions(report: ExecutionReport, request: Request,
                         x_bridge_token: str | None = Header(default=None)):
        _check_token(x_bridge_token)
        data = report.model_dump()
        if not data.get("signal_id") and data.get("seq"):
            row = request.app.state.store.get(int(data["seq"]))
            if row is not None:
                data["signal_id"] = row["signal_id"]
        rid = request.app.state.store.insert_execution(data)
        return {"status": "ok", "id": rid}

    @app.get("/health")
    async def health(request: Request):
        llm = request.app.state.llm
        return JSONResponse({
            "status": "ok",
            "queue": request.app.state.pipeline.queue.qsize(),
            "ai_mode": cfg.ai.mode,
            "ai_reachable": (await llm.health()) if llm else None,
        })

    return app


def _default_app() -> FastAPI:
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv

        load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_config(os.environ.get("BRIDGE_CONFIG", "config.yaml")))


# Lazily created so tests can import create_app without a config file present.
def __getattr__(name: str):
    if name == "app":
        global app
        app = _default_app()
        return app
    raise AttributeError(name)
