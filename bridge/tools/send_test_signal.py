"""Fire a realistic test signal at the bridge (end-to-end smoke test).

    python tools/send_test_signal.py --url http://127.0.0.1:8000/webhook/tradingview --secret $WEBHOOK_SECRET
    python tools/send_test_signal.py --symbol XAUUSD --direction SELL

Uses current UTC time so it passes the staleness check, and a unique signal_id each run.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import httpx

SAMPLES = {
    "EURUSD": dict(entry=1.08500, atr=0.00110, digits=5),
    "GBPUSD": dict(entry=1.27000, atr=0.00140, digits=5),
    "USDJPY": dict(entry=151.200, atr=0.180, digits=3),
    "XAUUSD": dict(entry=2650.00, atr=4.50, digits=2),
}


def build(symbol: str, direction: str, secret: str) -> dict:
    s = SAMPLES[symbol]
    e, a, d = s["entry"], s["atr"], s["digits"]
    k = 1 if direction == "BUY" else -1
    risk = 1.2 * a
    now = time.time()
    closes = [round(e - k * a * (1.5 - i * 0.08), d) for i in range(20)]
    return {
        "secret": secret,
        "signal_id": f"{symbol}-15-{int(now * 1000)}",
        "source": "tradingview", "strategy": "ATR-EW", "schema": 1,
        "symbol": symbol, "tv_ticker": f"OANDA:{symbol}", "timeframe": "15",
        "direction": direction, "setup": "W3",
        "entry": round(e, d), "sl": round(e - k * risk, d),
        "tp1": round(e + k * 1.0 * risk, d), "tp2": round(e + k * 2.0 * risk, d), "tp3": round(e + k * 3.2 * risk, d),
        "atr": a, "atr_ratio": 1.15, "adx": 24.0, "ema_trend": round(e - k * 2 * a, d), "risk_atr": 1.2,
        "pivots": [round(e - k * 3.0 * a, d), round(e - k * 0.2 * a, d), round(e - k * 1.6 * a, d)],
        "recent_closes": closes,
        "bar_time": int((now - 900) * 1000),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/webhook/tradingview")
    ap.add_argument("--secret", default=os.environ.get("WEBHOOK_SECRET", ""))
    ap.add_argument("--symbol", default="EURUSD", choices=sorted(SAMPLES))
    ap.add_argument("--direction", default="BUY", choices=["BUY", "SELL"])
    args = ap.parse_args()
    body = build(args.symbol, args.direction, args.secret)
    r = httpx.post(args.url, content=json.dumps(body), headers={"Content-Type": "application/json"}, timeout=10)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
