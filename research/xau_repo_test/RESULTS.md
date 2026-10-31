# Test of github.com/a1shmuk/xauusd-algo-trader (Phase 1, its live settings): **does not hold up**

**Setup:**
- Gold H1, Oct 2006 → Oct 2026 (Dukascopy).
- The repo's live settings: EMA 9/21 cross, RSI 50–65 / 35–50, H4 EMA50 trend, 13–17 server-time session, all 3 filters, SL = tighter of swing±$0.50 and 2×ATR, TP 3×ATR, RSI/EMA exits.
- Results in R (profit ÷ amount risked).

**Two modes:**
- *As written* reproduces the repo's look-ahead and optimistic fills.
- *Fixed* is the same rules, but:
  - the H4 trend comes only from closed H4 bars;
  - swing points are used only once confirmed;
  - when a bar hits both the stop and the target, the stop counts first;
  - commission is $7 per lot.

| Mode | Period | Trades | Win % | Avg R | PF | Return at 1% risk | Max DD |
|---|---|---|---|---|---|---|---|
| As written | 2024–26 (their tuning window) | 61 | 57.4 | +0.325 | 2.05 | +19.8% | 3.4% |
| **Fixed** | 2024–26 (their tuning window) | 51 | 52.9 | +0.173 | 1.47 | +8.8% | 3.4% |
| As written | 2006–23 (never tuned on) | 474 | 48.1 | +0.031 | 1.07 | +14.8% | 14.9% |
| **Fixed** | **2006–23 (never tuned on)** | **428** | **42.1** | **−0.105** | **0.79** | **−44.8%** | **47.4%** |

- **The repo's headline reproduces** when run as written: PF 2.05 / 57% / 3.4% DD here, against the 2.48 / 61% / 2.9% claimed. Different broker data and dates explain the gap.
- **The look-ahead and optimistic fills are about half of that** (+0.33R → +0.17R per trade in the tuning window).
- **On the 18 years it was never tuned on, it loses:** −0.105R per trade, PF 0.79. It made money in only 7 of 21 years.
- **Random-entry control** (300 runs, same session hours, same directions, same stop/target/exits): real −0.075R vs random −0.093R, **p = 0.31**. The entry timing is no better than random.
- **UTC session reading** (what the README says, instead of server time): fixed version −0.027R before 2024 and −0.030R in 2024–26. Also negative.
- **The repo's own pre-optimisation settings:** fixed version negative in both periods (−0.020R / −0.056R).

The SMC (order block / FVG) and macro layers aren't tested, because the repo never backtested them either.

Files: `repo_test.py` · `out_repo_test_summary.csv` · `out_repo_test_trades_fixed.csv` · `out_repo_test_trades_aswritten.csv` · `out_repo_test_random.csv`
