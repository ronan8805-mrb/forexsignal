# Dip-buy engine: demo setup (Windows + MT5)

The engine trades the one strategy that passed validation (`research/REPORT.md`, sections 9–12):

- **Buy** an index or ETF when its daily close is above its 200-day average and RSI(2) closes below 10.
- **Stop** at 3 × ATR(14) below entry. The stop sits on the broker's server, so it works even if your PC is off.
- **Exit** at the next open after RSI(2) closes above 70, or after 10 days.
- **Size** each trade so the stop loses 0.5% of equity.

It runs every 10 minutes, reads completed daily bars from your MT5 terminal, places orders on the demo account, and reports everything to your Telegram.

**Realistic expectations** (backtest, net of costs; not a promise):
- About 68% winners, small average gain per trade (+0.036R).
- About 2 signals a week across indices + ETFs. Some weeks have none; sell-offs bring several at once.
- Losing streaks happen: losses are about −1R each, wins are mostly small.
- Results come from many trades over months, not from any single week.

---

## Step 1: MT5 demo (once)

1. Install your broker's MetaTrader 5 and log in to the **demo** account.
2. Menu **Tools → Options → Expert Advisors** → tick **Allow algorithmic trading** → OK.
3. Make sure the **Algo Trading** button in the toolbar is **green**.
4. Leave MT5 open. The engine talks to this terminal.

## Step 2: install the engine (once)

Open PowerShell (no admin needed):

```powershell
cd C:\Users\User\forexsignal
powershell -ExecutionPolicy Bypass -File scripts\setup_dipbuy.ps1
```

This creates `dipbuy\.venv` (Python 3.13), installs the MetaTrader5 package, and creates `dipbuy\config.toml` with **dry_run = true**.

## Step 3: test Telegram and the broker connection

```powershell
cd C:\Users\User\forexsignal\dipbuy
.\.venv\Scripts\python.exe engine.py test-telegram
.\.venv\Scripts\python.exe engine.py check
```

- `test-telegram`: you should get "Telegram works". It reuses the bot token and chat ID from `bridge\.env`.
- `check`: shows **DEMO**, then a table of the instruments found at your broker, with today's close, SMA200, RSI(2), ATR and signal.
  - "not enough daily history yet": MT5 is still downloading. Run `check` again after a minute.
  - Index missing but you can see it in MT5's Market Watch under another name? Add it to `config.toml`:
    ```toml
    [symbols]
    US500 = "US500.cash"
    ```
  - Most brokers don't offer ETFs. That's fine; the engine trades what exists. With only index CFDs, expect about one signal a week.
  - "Listed but TRADING DISABLED": the server shows prices but won't take orders. MetaQuotes-Demo does this for its indices and SPY/QQQ/DIA, so there the engine trades the ETFs it can.
  - XLI on MetaQuotes-Demo has only ~176 days of history. It switches itself on once it reaches 202.

## Step 4: dry run (a day or two)

Start the scheduler:

```powershell
cd C:\Users\User\forexsignal
powershell -ExecutionPolicy Bypass -File scripts\install_dipbuy_task.ps1
```

With `dry_run = true` no orders are sent. Telegram messages start with "🧪 DRY RUN" and show what the engine *would* do.

Keep the PC awake while it runs: `powercfg /change standby-timeout-ac 0`

ETFs only trade 14:30–21:00 UK time. A signal from a day's close is bought at the next session's open, usually within 10 minutes of it.

## Step 5: trade the demo account

1. Open `dipbuy\config.toml` in Notepad.
2. Change `dry_run = true` to `dry_run = false` and save.
3. Nothing else to do: the next scheduled run uses the new setting.

The engine **refuses to place orders on a real-money account** unless you also set `allow_live = true`. Leave that off.

## Daily use

| You want to… | Do this (in `C:\Users\User\forexsignal\dipbuy`) |
|---|---|
| See open trades and results so far | `.\.venv\Scripts\python.exe engine.py status` |
| See every action | open `journal.csv` (Excel) or `engine.log` |
| Resume after an automatic pause | review first, then `.\.venv\Scripts\python.exe engine.py resume` |
| Stop the engine | `schtasks /Change /TN "DipBuy engine" /DISABLE` (and `/ENABLE` to restart) |
| Remove it completely | `schtasks /Delete /TN "DipBuy engine" /F` |

**Margin:** on exchange-traded ETF accounts (like MetaQuotes-Demo) each position ties up its full value, about 13–18k at 0.5% risk. So a 100k account holds roughly 6 at once; extra signals are skipped with a message.

**Automatic pause:** new entries stop if equity falls 14% below its peak, or if the last 50 trades are net negative. Open trades keep their stops. Telegram tells you when this happens.

## Telegram messages

| Message | Meaning |
|---|---|
| 🟢 BUY … Filled | Order placed with its stop |
| 🔵 EXIT / ✅ / ❌ CLOSED … R | Trade closed; result in R (1R = the amount risked) |
| ⏭ Signal skipped | Position limit, daily limit, or not enough free margin (deepest dips are taken first) |
| ⌛ Entry expired | Market stayed closed or the spread too wide for 30 h |
| ⚠️ Algo Trading is switched off | Click the Algo Trading button in MT5; the engine retries |
| ⏸ Engine paused | Drawdown or losing-run limit hit; review, then `resume` |
| 🛑 Engine error | Usually MT5 closed or logged out; reopen it |

## Troubleshooting

| Problem | Fix |
|---|---|
| `MT5 initialize failed` | MT5 must be open and logged in. With several MT5s installed, set `terminal_path` in `config.toml` |
| `another run is in progress` | Normal if a run is still going; a stuck lock clears itself after 20 minutes |
| No Telegram messages at all | Check `engine.log`; run `test-telegram` |
| Order rejected (code …) | Send me the line from `engine.log` |
| Nothing happens for days | Normal: no instrument was in a qualifying dip. `check` shows the current RSI(2) values |

## Files

- `dipbuy/strategy.py`: the rules (the same maths as the validated backtest, checked bar by bar).
- `dipbuy/engine.py`: MT5 orders, risk limits, Telegram, journal.
- `dipbuy/config.toml`: your settings (git-ignored).
- `dipbuy/state.json`, `journal.csv`, `engine.log`: live state and history (git-ignored).
- Tests: `dipbuy/tests/test_replay.py` (engine vs backtest on real history), `dipbuy/tests/test_smoke.py`.
