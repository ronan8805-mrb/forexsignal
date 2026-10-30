"""End-to-end and unit tests for the bridge. No network: Telegram + LLM are mocked
through an httpx.MockTransport."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from app.ai_filter import LLMFilter, parse_llm_json  # noqa: E402
from app.config import AICfg, Config, FileDropCfg, RulesCfg, SymbolSpec, TelegramCfg, load_config  # noqa: E402
from app.formatter import format_signal, parse_machine_line  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import TVSignal, level_errors  # noqa: E402
from app.telegram import TelegramSender  # noqa: E402
from send_test_signal import build  # noqa: E402

SECRET = "s3cret"
TOKEN = "tok"


class FakeNet:
    """Routes Telegram + Ollama calls; records what was sent."""

    def __init__(self, ai_score: int | None = 80, ai_down: bool = False, tg_fail_first: int = 0):
        self.ai_score, self.ai_down, self.tg_fail_first = ai_score, ai_down, tg_fail_first
        self.telegram: list[dict] = []
        self.tg_calls = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        if "api.telegram.org" in req.url.host:
            self.tg_calls += 1
            if self.tg_calls <= self.tg_fail_first:
                return httpx.Response(502, text="bad gateway")
            self.telegram.append(json.loads(req.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        if req.url.path in ("/api/tags",):
            return httpx.Response(503) if self.ai_down else httpx.Response(200, json={"models": []})
        if req.url.path == "/api/chat":
            if self.ai_down:
                raise httpx.ConnectError("refused", request=req)
            content = json.dumps({"score": self.ai_score, "reason": "test"})
            return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_cfg(tmp_path: Path, ai_mode: str = "shadow", **rules) -> Config:
    return Config(
        webhook_secret=SECRET,
        bridge_token=TOKEN,
        symbols={
            "EURUSD": SymbolSpec("EURUSD", ["OANDA:EURUSD"], 0.0001, 5),
            "USDJPY": SymbolSpec("USDJPY", ["OANDA:USDJPY"], 0.01, 3),
            "XAUUSD": SymbolSpec("XAUUSD", ["OANDA:XAUUSD", "GOLD"], 0.1, 2),
        },
        telegram=TelegramCfg(enabled=True, bot_token="123:abc", chat_id="-100", review_chat_id="42", max_retries=3),
        file_drop=FileDropCfg(enabled=True, dir=str(tmp_path / "inbox")),
        ai=AICfg(mode=ai_mode, min_score=55),
        rules=RulesCfg(**rules),
        sqlite_path=str(tmp_path / "db.sqlite"),
    )


def wait_status(client: TestClient, seq: int, timeout: float = 5.0) -> str:
    store = client.app.state.store
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = store.get(seq)
        if row["status"] != "received" and (row["status"] != "passed" or row["telegram_status"]):
            return row["status"]
        time.sleep(0.02)
    raise AssertionError("timeout waiting for pipeline")


# ---------------------------------------------------------------------------
def test_validation_ladder():
    p = build("EURUSD", "BUY", SECRET)
    p.pop("secret")
    s = TVSignal.model_validate(p)
    assert level_errors(s) == []
    p2 = dict(p, tp1=p["entry"] - 0.001)
    assert level_errors(TVSignal.model_validate(p2))
    sell = build("XAUUSD", "SELL", SECRET)
    sell.pop("secret")
    assert level_errors(TVSignal.model_validate(sell)) == []


def test_machine_line_roundtrip():
    p = build("USDJPY", "SELL", SECRET)
    p.pop("secret")
    s = TVSignal.model_validate(p)
    spec = SymbolSpec("USDJPY", [], 0.01, 3)
    msg = format_signal(s, spec)
    parsed = parse_machine_line(msg)
    assert parsed["sym"] == "USDJPY" and parsed["dir"] == "SELL"
    assert float(parsed["sl"]) == pytest.approx(s.sl, abs=1e-3)
    assert float(parsed["tp3"]) == pytest.approx(s.tp3, abs=1e-3)


def test_parse_llm_json_tolerant():
    assert parse_llm_json('<think>hmm</think>```json\n{"score": 71, "reason": "ok"}\n```') == (71.0, "ok")
    with pytest.raises(ValueError):
        parse_llm_json("no json here")


def test_end_to_end_pass(tmp_path):
    net = FakeNet(ai_score=80)
    app = create_app(make_cfg(tmp_path), transport=net.transport)
    with TestClient(app) as c:
        body = build("EURUSD", "BUY", SECRET)
        r = c.post("/webhook/tradingview", content=json.dumps(body))
        assert r.status_code == 200, r.text
        seq = r.json()["seq"]
        assert wait_status(c, seq) == "passed"
        row = c.app.state.store.get(seq)
        assert row["telegram_status"] == "sent"
        assert row["ai_score"] == 80
        # MT5 file drop
        files = list((tmp_path / "inbox").glob("*.sig"))
        assert len(files) == 1
        line = files[0].read_text().strip().split(",")
        assert line[0] == str(seq) and line[2] == "EURUSD" and line[3] == "BUY"
        # HTTP poll
        r = c.get("/api/v1/signals/pending?after=0", headers={"X-Bridge-Token": TOKEN})
        assert r.status_code == 200 and len(r.text.strip().splitlines()) == 3
        assert r.text.startswith("# latest_seq=")
        assert c.get("/api/v1/signals/pending?after=0").status_code == 401
        assert c.get(f"/api/v1/signals/pending?after={seq}", headers={"X-Bridge-Token": TOKEN}).text.count("\n") == 2
        # duplicate is acknowledged, not reprocessed
        r = c.post("/webhook/tradingview", content=json.dumps(body))
        assert r.json()["status"] == "duplicate"
        # telegram message carries machine line
        assert parse_machine_line(net.telegram[0]["text"])["sym"] == "EURUSD"


def test_auth_and_symbol_and_validation(tmp_path):
    app = create_app(make_cfg(tmp_path), transport=FakeNet().transport)
    with TestClient(app) as c:
        b = build("EURUSD", "BUY", "wrong")
        assert c.post("/webhook/tradingview", content=json.dumps(b)).status_code == 401
        b = build("EURUSD", "BUY", SECRET)
        b["symbol"] = "AUDNZD"
        b["tv_ticker"] = "OANDA:AUDNZD"
        assert c.post("/webhook/tradingview", content=json.dumps(b)).status_code == 422
        b = build("EURUSD", "BUY", SECRET)
        b["sl"] = b["entry"] + 0.001
        assert c.post("/webhook/tradingview", content=json.dumps(b)).status_code == 422
        assert c.post("/webhook/tradingview", content=b"not json").status_code == 400
        # alias resolution: GOLD -> XAUUSD
        b = build("XAUUSD", "SELL", SECRET)
        b["symbol"] = "GOLD"
        r = c.post("/webhook/tradingview", content=json.dumps(b))
        assert r.status_code == 200
        assert c.app.state.store.get(r.json()["seq"])["symbol"] == "XAUUSD"


def test_ai_veto_mode_blocks_and_notifies_review(tmp_path):
    net = FakeNet(ai_score=20)
    app = create_app(make_cfg(tmp_path, ai_mode="veto"), transport=net.transport)
    with TestClient(app) as c:
        seq = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "BUY", SECRET))).json()["seq"]
        assert wait_status(c, seq) == "vetoed"
        assert not list((tmp_path / "inbox").glob("*.sig"))
        assert net.telegram and net.telegram[0]["chat_id"] == "42"  # review chat only


def test_ai_shadow_mode_never_blocks(tmp_path):
    net = FakeNet(ai_score=5)
    app = create_app(make_cfg(tmp_path, ai_mode="shadow"), transport=net.transport)
    with TestClient(app) as c:
        seq = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "BUY", SECRET))).json()["seq"]
        assert wait_status(c, seq) == "passed"
        assert c.app.state.store.get(seq)["ai_verdict"] == "veto"  # logged, not enforced


def test_ai_down_fails_open(tmp_path):
    net = FakeNet(ai_down=True)
    app = create_app(make_cfg(tmp_path, ai_mode="veto"), transport=net.transport)
    with TestClient(app) as c:
        seq = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "BUY", SECRET))).json()["seq"]
        assert wait_status(c, seq) == "passed"
        assert c.app.state.store.get(seq)["ai_verdict"] == "unavailable"


def test_daily_cap_and_stale(tmp_path):
    app = create_app(make_cfg(tmp_path, ai_mode="off", max_per_day_per_symbol=1), transport=FakeNet().transport)
    with TestClient(app) as c:
        s1 = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "BUY", SECRET))).json()["seq"]
        assert wait_status(c, s1) == "passed"
        time.sleep(0.002)
        s2 = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "SELL", SECRET))).json()["seq"]
        assert wait_status(c, s2) == "rejected"
        assert "daily cap" in c.app.state.store.get(s2)["reject_reason"]
        old = build("USDJPY", "BUY", SECRET)
        old["timestamp"] = "2020-01-01T00:00:00Z"
        s3 = c.post("/webhook/tradingview", content=json.dumps(old)).json()["seq"]
        assert wait_status(c, s3) == "rejected"
        assert "stale" in c.app.state.store.get(s3)["reject_reason"]


def test_rule_filter_min_rr(tmp_path):
    app = create_app(make_cfg(tmp_path, ai_mode="off", min_rr_tp2=5.0), transport=FakeNet().transport)
    with TestClient(app) as c:
        seq = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "BUY", SECRET))).json()["seq"]
        assert wait_status(c, seq) == "vetoed"


def test_executions_endpoint(tmp_path):
    app = create_app(make_cfg(tmp_path, ai_mode="off"), transport=FakeNet().transport)
    with TestClient(app) as c:
        rep = {"account": "1001", "signal_id": "EURUSD-15-1", "event": "CLOSE", "profit": 25.0, "r_multiple": 1.0}
        assert c.post("/api/v1/executions", json=rep).status_code == 401
        assert c.post("/api/v1/executions", json=rep, headers={"X-Bridge-Token": TOKEN}).status_code == 200
        # seq -> signal_id resolution
        seq = c.post("/webhook/tradingview", content=json.dumps(build("EURUSD", "BUY", SECRET))).json()["seq"]
        wait_status(c, seq)
        rep2 = {"account": "1001", "seq": seq, "event": "OPEN", "volume": 0.1}
        assert c.post("/api/v1/executions", json=rep2, headers={"X-Bridge-Token": TOKEN}).status_code == 200
        rows = c.app.state.store.query("SELECT signal_id FROM executions WHERE event='OPEN'")
        assert rows[0]["signal_id"] == c.app.state.store.get(seq)["signal_id"]


def test_telegram_retries_then_succeeds():
    net = FakeNet(tg_fail_first=2)
    tg = TelegramSender("1:x", max_retries=3, client=httpx.AsyncClient(transport=net.transport), base_delay=0.0)
    assert asyncio.run(tg.send("-100", "hi")) == "sent"
    assert net.tg_calls == 3


def test_telegram_gives_up_on_4xx():
    def h(req):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    tg = TelegramSender("1:x", max_retries=5, client=httpx.AsyncClient(transport=httpx.MockTransport(h)), base_delay=0.0)
    assert asyncio.run(tg.send("-1", "hi")).startswith("failed:400")


def test_llm_openai_compat_backend():
    def h(req):
        assert req.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 60, "reason": "fine"}'}}]})

    cfg = AICfg(mode="veto", backend="openai_compat", base_url="http://x:8080", min_score=55)
    llm = LLMFilter(cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    p = build("EURUSD", "BUY", SECRET)
    p.pop("secret")
    res = asyncio.run(llm.evaluate(TVSignal.model_validate(p)))
    assert res.verdict == "pass" and res.score == 60


def test_example_config_loads(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "x")
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert cfg.resolve_symbol("OANDA:XAUUSD") == "XAUUSD"
    assert cfg.resolve_symbol("FX_IDC:USDJPY") == "USDJPY"
    assert cfg.resolve_symbol("PEPPERSTONE:EURUSD") == "EURUSD"  # prefix fallback
    assert cfg.ai.mode == "shadow"
