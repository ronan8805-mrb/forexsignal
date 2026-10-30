# ATR-EW Signal Stack

ATR volatility filter + Elliott-Wave swing structure → TradingView signal → Python bridge (validation, caps, local AI score) → Telegram + MT5 execution with partial take-profits.

**There are no performance claims in this repo.** The stack is built so performance can be *measured*: the TradingView Strategy Tester backtests the exact signal logic, every signal and verdict is logged in SQLite, and the EA reports every fill/partial/close back with an R-multiple. `bridge/tools/report.py` turns that into numbers.

---

## 1. Architecture

```
┌──────────────────────────── TradingView (cloud) ────────────────────────────┐
│  pine/atr_ew_hybrid.pine  (one chart per symbol: EURUSD GBPUSD USDJPY XAUUSD, │
│                            M15 or H1)                                         │
│   ATR regime filter ─┐                                                        │
│   ATR zigzag pivots ─┼─► W3 / ABC setup ─► SL (structure ∧ ATR) ─► TP1..TP3   │
│   ADX / session ─────┘                       │                                │
│                                   strategy.entry/exit (backtest = measurement)│
│                                   alert(JSON) ──────────────────────┐         │
└─────────────────────────────────────────────────────────────────────┼─────────┘
                                         HTTPS POST (port 443 only)   │
                                                                      ▼
┌────────────────────────── Your PC / VPS ─────────────────────────────────────┐
│  bridge/  FastAPI  (uvicorn :8000, exposed via Cloudflare Tunnel/nginx :443)  │
│                                                                               │
│   /webhook/tradingview                                                        │
│     secret check ─► schema + price-ladder validation ─► dedup (signal_id)     │
│     ─► SQLite insert ─► 200 OK  (fast; TradingView times out quickly)         │
│                         │                                                     │
│                         ▼  async worker queue                                 │
│     staleness ─► daily caps ─► RuleFilter (R:R, stop width)                   │
│                              ─► LLMFilter ───────────────► Ollama / llama.cpp │
│                                 (off | shadow | veto,       qwen2.5-7B Q4     │
│                                  FAIL-OPEN on any error)    on the RTX 5090   │
│        ├── vetoed ──► SQLite + optional Telegram review chat                 │
│        └── passed ──► MT5 dispatch ──► Telegram channel (retries)            │
│                         │                                                     │
│         ┌───────────────┴────────────────┐                                    │
│         ▼                                ▼                                    │
│   File drop (same PC)              HTTP poll (remote / VPS terminals)         │
│   Common\Files\ATR_EW\inbox\*.sig  GET /api/v1/signals/pending (port 80/443)  │
└─────────┬────────────────────────────────┬────────────────────────────────────┘
          ▼                                ▼
┌──────────────────────── MetaTrader 5 terminal(s) ────────────────────────────┐
│  mt5/ATR_EW_Executor.mq5   (attach to ONE chart; manages all symbols)        │
│   dedup per account ─► risk gate (DD pause, daily loss, trades/day, max open) │
│   ─► entry checks (age, deviation in R, spread vs stop, stops level)          │
│   ─► lot = equity × risk% ÷ loss-per-lot(OrderCalcProfit) × copy multiplier   │
│   ─► market order with broker-side SL + TP3                                   │
│   ─► timer: TP1 partial + BE ─► TP2 partial + SL→TP1 ─► TP3 broker-side       │
│   ─► OnTradeTransaction: journal CSV + POST /api/v1/executions (R-multiple)   │
└──────────────────────────────────────────────────────────────────────────────┘
                                 ▼
                    bridge/tools/report.py  (funnel, R by symbol/setup, AI shadow split)
```

Each layer only depends on the wire format of the one before it:

| Boundary | Contract | Defined in |
|---|---|---|
| Pine → bridge | JSON schema 1 | `f_json()` in the Pine script; `TVSignal` in `bridge/app/models.py` |
| bridge → MT5 | one CSV line: `seq,signal_id,symbol,direction,entry,sl,tp1,tp2,tp3,received_unix` | `bridge/app/dispatch.py`; `ParseRow()` in the EA |
| bridge → Telegram | readable block + `#SIG v1 key=value…` line | `bridge/app/formatter.py` (`parse_machine_line()` is the inverse) |
| EA → bridge | execution report JSON | `ExecutionReport` in `bridge/app/main.py` |

You can replace any layer (another charting tool, a different execution venue, a bigger model) without touching the others.

---

## 2. Strategy rules (summary — full comments in the Pine file)

**Regime filter.** `ATR(14)/SMA(ATR,100)` must be between 0.8 and 2.5, ADX(14) ≥ 18, and the bar must fall in the 07:00–20:00 UTC session.

**Swings.** Pivots come from `ta.pivothigh/low(5,3)`. They're confirmed 3 bars late and never repaint. A swing in the opposite direction is accepted only if it's at least 1.0×ATR from the previous swing. A same-side pivot replaces the previous one if it's more extreme.

**Setup A: W3 (wave-3 entry).**
- Long: L0 → H1 → L2, with L2 > L0 (wave 2 can't retrace past the start of wave 1).
- Wave-2 retrace is 0.382–0.786 of wave 1, and wave 1 is at least 2×ATR.
- Trigger: close above `L2 + TrigFrac·(H1−L2)`.
- Targets: L2 + {1.0, 1.618, 2.618}×W1.

**Setup B: ABC (trend resumption).**
- Long: H0 → A → B → C, with B < H0 and C < A.
- B retraces 0.236–0.886 of A, and C/A is between 0.618 and 1.618.
- Close must be above EMA200.
- Trigger: close above `C + TrigFrac·(B−C)`.
- Targets: C + {1.0, 1.618, 2.618}×(H0−C).

**Stops.** The default is the tighter of two stops:
- the structural stop (invalidation pivot ± 0.25 ATR)
- the ATR stop (1.5 ATR)

The stop is never closer than 0.5 ATR. If it would be wider than 3 ATR, the signal is skipped.

**Asymmetric targets by construction.** Each TP is whichever is further: the Fibonacci target, or the R floor (1R / 2R / 3R). The default exit plan is 40% at TP1, 30% at TP2, and the rest at TP3. After TP1 the stop moves to break-even; after TP2 it trails to TP1.

**Frequency.** The 5–6 signals/day target is controlled by these inputs: `Min swing`, `W2 range`, `TrigFrac`, `Cooldown`, `Max signals/day` (per chart), and the bridge `max_per_day_total`. Tune them against Strategy Tester trade counts. Don't assume a frequency; measure it.

---

## 3. Deployment order

Do the steps in order, and confirm each one before moving to the next.

**Step 1 — Pine backtest only (no money, no infrastructure).**
1. Paste `pine/atr_ew_hybrid.pine` into the Pine Editor and add it to the chart.
2. Open it on EURUSD M15 and review the Strategy Tester: trade count per day, average R, max drawdown, and how many trades reach TP2 or TP3.
3. Repeat on GBPUSD, USDJPY and XAUUSD, and on H1.
4. In *Properties*, set commission and slippage to realistic values (see §6).

**Step 2 — Bridge + Telegram (signals only).**
```bash
cd bridge
python -m venv .venv && .venv\Scripts\activate          # Windows  (source .venv/bin/activate on Linux)
pip install -r requirements.txt
copy config.example.yaml config.yaml                      # edit symbols/paths
copy .env.example .env                                    # fill secrets
python -m pytest -q                                       # 15 tests, no network needed
uvicorn app.main:app --host 127.0.0.1 --port 8000
python tools/send_test_signal.py --secret <WEBHOOK_SECRET> # should arrive in Telegram
```
Telegram setup:
1. Create a bot with @BotFather and copy the token into `.env`.
2. Add the bot to your channel as an admin.
3. Get the chat id (send a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates`). Channel ids look like `-100…`.

**Step 3 — Public HTTPS endpoint for TradingView.**
- TradingView webhooks only post to ports 80/443.
- Use a VPS with nginx + TLS, or a Cloudflare Tunnel pointed at `http://127.0.0.1:8000`, e.g. `cloudflared tunnel --url http://127.0.0.1:8000`. Use a named tunnel for production.
- Webhooks need a paid TradingView plan with 2FA enabled.

**Step 4 — TradingView alerts.** Do this once per chart (symbol × timeframe):
1. Set the *Webhook shared secret* input to `WEBHOOK_SECRET`.
2. Create an Alert: Condition = *ATR-EW Hybrid Signals*, then **"alert() function calls only"**.
3. Under Notifications, tick *Webhook URL* and enter `https://<your-domain>/webhook/tradingview`.
4. Leave the message box empty; the script builds the JSON itself.
5. Expiration: open-ended (or renew periodically).

**Step 5 — AI filter in shadow mode.** See §5. Run it for weeks in `mode: shadow` before you consider `veto`.

**Step 6 — MT5 EA on a demo account.**
1. Copy `mt5/ATR_EW_Executor.mq5` into `MQL5\Experts\` and compile it in MetaEditor (F7).
2. Attach it to **one** chart and enable Algo Trading.
3. File-drop mode (bridge on the same PC):
   - Set the bridge `mt5.file_drop.dir` to `%APPDATA%\MetaQuotes\Terminal\Common\Files\ATR_EW\inbox`.
   - In the EA, keep `InpSource=SOURCE_FILE`.
4. HTTP mode (EA on another machine):
   - MT5 `WebRequest` only uses **ports 80/443**, so put the bridge behind nginx or a tunnel on 443.
   - Whitelist the URL under Tools → Options → Expert Advisors.
   - Set `InpSource=SOURCE_HTTP`, `InpBridgePollUrl`, and `InpBridgeToken`.
5. Map broker symbols, e.g. `InpSymbolMap = XAUUSD=GOLD;EURUSD=EURUSD.m`, or set `InpSymbolSuffix`.
6. Optional: set `InpReportToBridge=true` (same port 80/443 rule) to feed `tools/report.py`.
7. Start with `InpExecMode=EXEC_ALERT_ONLY` for a few days. You get phone pushes and review every trade manually. Then switch to `EXEC_AUTO` on demo, and only after that go live.

**Step 7 — Measure.** Run `python tools/report.py --db data/signals.db --days 30` weekly. Compare the live realised R per setup against the Step 1 backtest. A large gap usually means slippage, feed mismatch, or late alerts.

### Copy-trading modes
- **Several accounts on one PC:**
  - Run the EA in each terminal with `SOURCE_FILE`. Common\Files is shared and the EA never deletes inbox files.
  - Each account keeps its own processed-id list (`ATR_EW\state\processed_<login>.txt`), its own risk %, and its own `InpCopyMultiplier`.
- **Followers on other machines/VPS:** use `SOURCE_HTTP` against the same bridge. Every follower tracks its own `after=<seq>` cursor.
- **MQL5 Signals service:** run the EA in `EXEC_AUTO` on the provider account. Subscribers copy through MetaQuotes; nothing else is needed.
- **Manual followers:** use the Telegram channel. The `#SIG v1` line can be parsed by any third-party copier bot.

---

## 4. Configuration examples

**`bridge/.env`**
```
WEBHOOK_SECRET=9f3c…long-random…
BRIDGE_TOKEN=4a71…long-random…
TELEGRAM_BOT_TOKEN=123456789:AAE…
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_REVIEW_CHAT_ID=987654321
```

**Webhook URL (TradingView alert):** `https://signals.example.com/webhook/tradingview`

**Symbol map:**
- Bridge `config.yaml`: TradingView alias → canonical symbol.
- EA input: canonical symbol → broker symbol.
```yaml
symbols:
  XAUUSD: {aliases: ["OANDA:XAUUSD", "TVC:GOLD", "GOLD"], pip: 0.1, digits: 2}
```
```
InpSymbolMap = XAUUSD=GOLD;EURUSD=EURUSD.m;GBPUSD=GBPUSD.m;USDJPY=USDJPY.m
```

**Ollama model:** `ai.model: qwen2.5:7b-instruct-q4_K_M` (alternative: `llama3.1:8b-instruct-q4_K_M`)

**Example Telegram message**
```
🟢 BUY EURUSD  M15 · W3
Entry 1.08500
SL    1.08368  (13.2 pips)
TP1   1.08632  (1.0R)
TP2   1.08764  (2.0R)
TP3   1.08922  (3.2R)
ATR 0.00110 · ATRx 1.15 · ADX 24
AI (shadow): 72/100 PASS — momentum resumed after W2, stop outside noise
#SIG v1 id=EURUSD-15-1730… sym=EURUSD dir=BUY entry=1.08500 sl=1.08368 tp1=… tp2=… tp3=… tf=15 ts=…
```

---

## 5. Local AI filter (RTX 5090, 32 GB)

```bash
# Ollama (Windows installer from ollama.com, then:)
ollama pull qwen2.5:7b-instruct-q4_K_M      # ~4.7 GB VRAM; fits many times over in 32 GB
ollama serve                                # default http://127.0.0.1:11434
curl http://127.0.0.1:11434/api/tags        # bridge /health shows ai_reachable=true
```
**llama.cpp instead of Ollama:**
- Run `llama-server -m qwen2.5-7b-instruct-q4_k_m.gguf -ngl 99 --port 8080`.
- In the config, set `ai.backend: openai_compat` and `ai.base_url: http://127.0.0.1:8080`.

**What the model sees.** One compact, scale-free JSON per signal: the direction, the setup and what its pivots mean, the TP ladder in R, risk in ATR, ATR ratio, ADX, distance from the trend EMA in ATR, the pivots relative to entry in ATR, and the last 20 closes relative to entry in ATR. It returns `{"score":0-100,"reason":"…"}` at temperature 0.

**Modes.**
- `off`: rules only.
- `shadow`: scores are logged and shown in Telegram, but never block a signal.
- `veto`: a score below `min_score` blocks the signal.

**Fail-open.** If the model times out, the connection is refused, or the output isn't valid JSON, the verdict is `unavailable` and the signal **passes**. The core pipeline never waits on the GPU.

**Honest framing.** A 7B model reading 20 closes is a consistency check, not an edge. It may catch obviously late or overextended entries. Keep it in `shadow` until `report.py` shows that the would-veto group performs clearly worse than the would-pass group over a meaningful sample (well over 100 trades). Swapping the model, the prompt (`SYSTEM_PROMPT` in `bridge/app/ai_filter.py`) or the context requires no other changes.

---

## 6. Slippage and execution assumptions

- **Entry is the bar close.** The alert fires on bar close. Expect 1–5 s from TradingView to the bridge, under 1 s through the bridge (more with the LLM: 7B Q4 on a 5090 is typically well under a second, but set `timeout_seconds` accordingly), and 0–2 s for the file poll. The EA fills at market, not at the signal's `entry`.
- **Deviation guard.** The EA skips a signal if the market has moved more than `InpMaxEntryDeviationR` (default 0.30R) from `entry`. It keeps the signal's absolute SL and TP levels and sizes the position from the *actual* price to SL, so the money at risk stays at risk% even after slippage.
- **Feed mismatch.** TradingView's feed (e.g. OANDA) is not your broker's feed. On EURUSD the difference is usually small. On XAUUSD it can be a meaningful fraction of a tight stop. Pick the TradingView feed closest to your broker, and watch the `slip=` field in the journal.
- **Spread.** The EA skips a signal if the spread is more than 15% of the stop distance. That mostly bites on XAUUSD at rollover and around news.
- **Backtest settings.** The Pine `strategy()` header uses `slippage=2` ticks and zero commission as placeholders. Set realistic values in Properties: your broker's commission per lot, and slippage of roughly 1–3 pips on majors and 20–50 cents on gold. The Strategy Tester fills at the next bar's open, which is close to how live trading behaves.
- **Partial-TP mismatch.** TradingView fills partials at the exact limit price. The EA closes partials at market once bid/ask touches TP1/TP2, so expect small negative slippage on partials. TP3 and SL are broker-side orders.

---

## 7. Where human review adds value

1. **Before trusting a setup:** read the Strategy Tester trade list, and pick out the W3 vs ABC trades by their entry comment. Throw away parameter sets that only work on one symbol or one year.
2. **Shadow period:** run the AI in `shadow` and the EA in `EXEC_ALERT_ONLY`. Compare each signal on the chart with the drawn zigzag and wave labels, and decide whether the wave count looks sane to you.
3. **The review chat:** the vetoed signals sent to `TELEGRAM_REVIEW_CHAT_ID` are where the filters can be wrong in either direction.
4. **News:** neither the rules nor the 7B model know the economic calendar. Pausing around NFP, CPI and FOMC (or setting the session filter) is a human decision.
5. **Drawdown pause:** the EA stops new entries at `InpMaxDrawdownPct` and **requires a manual reset** (`InpResetDrawdownPause=true`, then back to false). That is deliberate, so a human looks before trading resumes.
6. **Parameter changes:** change one thing at a time, and compare `report.py` output before and after over a similar number of trades.

---

## 8. Verification status

- **Python bridge:** 15 automated tests pass, covering auth, validation, dedup, caps, staleness, shadow/veto/fail-open AI, Telegram retry and 4xx handling, the file drop, HTTP poll and execution reports. A live uvicorn smoke test also passed.
- **Pine and MQL5:** could not be compiled in the build environment. Both files had a line-by-line API and logic review, and the issues it found were fixed:
  - Pine: break-even sizing, trail to TP1 after TP2, `max_bars_back`, and the daily counter now resets on the UTC day.
  - EA: position-id resolution from the fill deal, no silent naked positions, a global-variable prefix that can't collide with the drawdown peak, partial closes on netting accounts, tick-size rounding, CLOSE vs PARTIAL taken from deal history, and HTTP cursor resync.

  **Compile both in TradingView and MetaEditor first** and fix any build-specific complaints before demo trading. Netting accounts: the EA refuses new entries while *any* position exists on that symbol.

## 9. Repo layout

```
pine/atr_ew_hybrid.pine           TradingView strategy + JSON alerts
bridge/app/main.py                FastAPI endpoints, lifecycle
bridge/app/pipeline.py            validate → caps → filters → dispatch → Telegram
bridge/app/ai_filter.py           RuleFilter, LLMFilter (Ollama / OpenAI-compatible), fail-open
bridge/app/telegram.py            sender with retry/backoff/429 handling
bridge/app/formatter.py           human + machine message format, parser
bridge/app/dispatch.py            MT5 CSV line, atomic file drop, pruning
bridge/app/store.py               SQLite (signals, executions)
bridge/app/models.py, config.py   schema, validation, config loading
bridge/tools/send_test_signal.py  end-to-end smoke test
bridge/tools/report.py            measurement report
bridge/tests/test_bridge.py       15 tests (mocked Telegram + LLM)
mt5/ATR_EW_Executor.mq5           Expert Advisor
```
