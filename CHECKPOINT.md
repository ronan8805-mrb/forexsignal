# Checkpoint: 31 Oct 2026 (work paused by request)

## Live right now (left running, untouched)
- **Dip-buy engine**: `dipbuy/engine.py`, Windows scheduled task **"DipBuy engine"**, every 10 minutes.
  - **Account:** MetaQuotes-Demo 114908745, `dry_run = false`, Algo Trading on.
  - **Trades:** 11 US ETFs (IWM DVY EFA XLF XLK XLE XLV IYR SMH ARKQ JETS; XLI joins once it has 202 days of history). Indices, SPY, QQQ and DIA are quotes-only on this server.
  - **Logs:** heartbeat in `dipbuy/engine.log`, trades in `dipbuy/journal.csv`. Telegram errors are throttled to one an hour.
  - **Stop:** `schtasks /Change /TN "DipBuy engine" /DISABLE` · **Status:** `.\.venv\Scripts\python.exe engine.py status`
  - **Guide:** `docs/DIPBUY_SETUP.md`.
- **Not done:** the IC Markets demo login (MT5 rejected the password). The engine trades any account MT5 is logged into, and `check` shows which markets are tradable.

## Research status (all in `research/`)
| Item | Verdict | Where |
|---|---|---|
| Daily RSI(2) dip-buy | **Only strategy that passed** (PRIMARY +0.043R, PF 1.23, beats random p ≈ 0.01) | REPORT.md §9–12 |
| Original ATR + Elliott Wave | Rejected: no edge | REPORT.md §0–4 |
| 1-hour dip-buy | Rejected (−0.043R) | REPORT.md §11 |
| VWAP pullback / reclaim / band fade | Rejected (−0.15 / −0.15 / −0.11R) | REPORT.md §13, VWAP_PREREG.md |
| Fear filter (VIX term structure, put/call, GEX, breadth, candle proxy) | **Rejected**: gain not distinguishable from luck (p 0.052 / 0.165) | fear_audit/FEAR_AUDIT.md |
| a1shmuk/xauusd-algo-trader (GitHub) | Rejected: in-sample + look-ahead; −0.105R on 2006–23 | xau_repo_test/RESULTS.md |
| Improved gold V2 (H4 EMA cross + daily trend + trailing stop) | **Candidate only**: +0.123R (walk-forward +0.171R); fragile to costs and stop slippage; fails on silver, FX and indices | xau_repo_test/IMPROVE_RESULTS.md |

## Paused / possible next steps
1. Optional: a separate demo engine for gold V2. Not built; must not touch the dip-buy engine.
2. Optional: an IC Markets demo, to add index CFDs.
3. Optional: rotate the GitHub, Telegram and ngrok tokens that were shared in chat (you chose to leave them).

## Not in git (on the PC only, re-creatable)
- Market data: `research/data*`, `research/fear_audit/data*`.
- Local settings: `dipbuy/config.toml`, `bridge/.env`.
- Large outputs: `research/*.pkl`.
