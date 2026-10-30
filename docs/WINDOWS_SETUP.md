# Running the stack on your Windows PC

Everything runs on the one PC: the bridge, MT5, Ollama (RTX 5090), and a tunnel that gives TradingView a public HTTPS URL.

```
TradingView ──HTTPS──► https://your-name.ngrok-free.app ──► ngrok agent (PC)
                                                              │
                                                              ▼
                                            bridge  http://127.0.0.1:8000
                                     ├─► Ollama  http://127.0.0.1:11434
                                     ├─► Telegram (outbound HTTPS)
                                     └─► %APPDATA%\MetaQuotes\Terminal\Common\Files\ATR_EW\inbox ─► MT5 EA
```

Nothing listens on your router and no ports are opened: the tunnel is an outbound connection.
MT5 talks to the bridge through files, so the EA does not need WebRequest or ports 80/443.

> **Not Vercel.** The bridge needs a persistent disk (SQLite), a background worker, the MT5 folder and Ollama, and serverless hosting provides none of them. Delete the Vercel project (Vercel → project → Settings → Delete), or disconnect the Git repo, so every push stops triggering a failed build.

---

## 0. Prerequisites (one time)

| Tool | Install |
|---|---|
| Python 3.11+ | `winget install Python.Python.3.12` (tick "Add to PATH" if you use the installer) |
| Git | `winget install Git.Git` |
| ngrok | `winget install ngrok.ngrok`, then sign up free at ngrok.com |
| Ollama | `winget install Ollama.Ollama` (it runs in the tray and starts with Windows) |
| MetaTrader 5 | from your broker |

## 1. Get the code

```powershell
cd $HOME
git clone https://github.com/ronan8805-mrb/forexsignal
cd forexsignal\bridge
copy config.example.yaml config.yaml
copy .env.example .env
notepad .env
```

In `.env`:

- **`WEBHOOK_SECRET`** and **`BRIDGE_TOKEN`**: two long random strings. Generate each one with:
  ```powershell
  [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
  ```
- **`TELEGRAM_BOT_TOKEN`**: from @BotFather.
- **`TELEGRAM_CHAT_ID`**: your channel id, e.g. `-100…`. To find it, post in the channel, then open `https://api.telegram.org/bot<TOKEN>/getUpdates`.
- **`NGROK_DOMAIN`**: set this in step 3.

`config.yaml` works as-is for EURUSD, GBPUSD, USDJPY and XAUUSD. The MT5 inbox path uses `%APPDATA%`, which Windows expands automatically.

## 2. Test the bridge locally

```powershell
cd $HOME\forexsignal
powershell -ExecutionPolicy Bypass -File scripts\start_bridge.ps1
```

On the first run this creates `bridge\.venv` and installs the dependencies. Then, in a **second** PowerShell window:

```powershell
cd $HOME\forexsignal\bridge
.\.venv\Scripts\python -m pytest -q                          # expect: 15 passed
.\.venv\Scripts\python tools\send_test_signal.py --secret <WEBHOOK_SECRET>
```

Expect three things:

- `200 {"status":"accepted",...}`
- a message in your Telegram channel
- a `.sig` file in `%APPDATA%\MetaQuotes\Terminal\Common\Files\ATR_EW\inbox`

## 3. Public URL with ngrok (static, free)

1. **Domains:** in the ngrok dashboard, open **Domains** and claim your free static domain, e.g. `your-name.ngrok-free.app`.
2. **Authtoken:** in the dashboard, open **Your Authtoken** and run `ngrok config add-authtoken <token>`.
3. **`.env`:** put `NGROK_DOMAIN=your-name.ngrok-free.app` in `bridge\.env`.
4. **Start the tunnel.** Keep the bridge running and start it in another window:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start_tunnel.ps1
   ```
5. **Test through the public URL:**
   ```powershell
   cd $HOME\forexsignal\bridge
   .\.venv\Scripts\python tools\send_test_signal.py --url https://your-name.ngrok-free.app/webhook/tradingview --secret <WEBHOOK_SECRET>
   ```

The URL stays the same across restarts, so TradingView alerts never need editing.

**Alternative:** if you own a domain on Cloudflare, a named Cloudflare Tunnel works too. It gives a stable `signals.yourdomain.com`. A free *quick* tunnel (`trycloudflare.com`) gets a new URL on every restart, which would break your alerts, so don't use one.

## 4. TradingView alerts

These need a paid plan with 2FA enabled. Do the following for each chart (symbol × timeframe):

1. Add `pine/atr_ew_hybrid.pine` to the chart and set **Webhook shared secret** to your `WEBHOOK_SECRET`.
2. Create an alert:
   - **Condition:** ATR-EW Hybrid Signals → **alert() function calls only**
   - **Notifications:** Webhook URL `https://your-name.ngrok-free.app/webhook/tradingview`
   - Leave the message box empty.

## 5. Ollama (AI filter, shadow mode)

```powershell
ollama pull qwen2.5:7b-instruct-q4_K_M
curl.exe http://127.0.0.1:8000/health      # "ai_reachable": true
```

`config.yaml` ships with `ai.mode: shadow`, so the filter scores and logs signals but never blocks them.

## 6. MT5 Expert Advisor

1. In MT5, open **File → Open Data Folder → MQL5\Experts** and copy `mt5\ATR_EW_Executor.mq5` there.
2. Open it in MetaEditor and press **F7** to compile. Fix any build-specific complaints first.
3. Attach the EA to **one** chart, enable **Algo Trading**, and set these inputs:
   - `InpSource = SOURCE_FILE` (the default; no WebRequest setup needed)
   - `InpSymbolMap`, if your broker uses suffixes, e.g. `EURUSD=EURUSD.m;XAUUSD=GOLD`
   - `InpExecMode = EXEC_ALERT_ONLY` for the first days. You get phone pushes and review every trade yourself.
4. Run on a **demo** account until the journal (`Common\Files\ATR_EW\journal_<login>.csv`) and Telegram agree.

**Optional: execution reports to the bridge.** MT5 WebRequest only uses ports 80/443, so this needs three changes:

- `BRIDGE_PORT=80` in `.env`
- `InpReportToBridge=true` and `InpReportUrl=http://127.0.0.1/api/v1/executions` in the EA
- `http://127.0.0.1` added under **Tools → Options → Expert Advisors → Allow WebRequest**

Without this, the EA journal CSV still records everything.

## 7. Run unattended

From an **administrator** PowerShell:

```powershell
cd $HOME\forexsignal
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
Start-ScheduledTask "ATR-EW bridge"; Start-ScheduledTask "ATR-EW tunnel"
powercfg /change standby-timeout-ac 0          # a sleeping PC drops signals
```

Also turn on these two settings:

- MT5: **Tools → Options → Server → keep personal settings and data at startup**
- Windows: the auto-login or startup method you prefer, so MT5 relaunches with the EA after a reboot.

Logs:

- bridge: `bridge\logs\bridge.log`
- EA: MT5 **Experts** tab, plus the journal CSV
- signal history: `python tools\report.py --db data\signals.db`

## 8. Update later

```powershell
cd $HOME\forexsignal
git pull
Stop-ScheduledTask "ATR-EW bridge"; Start-ScheduledTask "ATR-EW bridge"
```

`.env`, `config.yaml` and `bridge\data\` are git-ignored, so pulls never overwrite your secrets or history.

## Troubleshooting

| Symptom | Check |
|---|---|
| TradingView alert fires, nothing arrives | `curl.exe https://your-name.ngrok-free.app/health` returns `"status":"ok"`; ngrok window shows the POST; secret matches (a browser shows an ngrok warning page first — that is normal on the free tier) |
| `401 bad secret` | Pine input *Webhook shared secret* ≠ `WEBHOOK_SECRET` |
| `422 unknown symbol` | add the TradingView ticker to `aliases` in `config.yaml` |
| Telegram silent | `telegram_status` column in `data\signals.db`; bot is admin in channel; chat id starts with `-100` |
| EA does nothing | Experts tab; Algo Trading enabled; `.sig` files present; `InpSymbolMap` matches broker names |
| `ai_reachable: false` | Ollama tray icon running; `ollama list` shows the model. Signals still pass (fail-open) |
