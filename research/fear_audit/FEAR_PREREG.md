# Fear-filter audit: pre-registration (written 2026-10-31, after reproducing the baseline and BEFORE any filtered result)

**Read-only audit.** No existing code, config, strategy file or the live engine is changed. Everything new lives in `research/fear_audit/`.

## 0. Baseline (reproduced exactly; `repro/`)
- `validate_mr.py` was run unchanged; its md5 was verified before and after. It reproduces every figure in REPORT §9, including the random-control p-values (same seed).
- PRIMARY (21 unseen equity): 1,307 trades, 71.0% wins, **+0.043R**, PF 1.23, C1/C2 p = 0.005 / 0.014.
  - 2013–22: +0.008R, PF 1.03. **2023–26 holdout: +0.082R, PF 1.59.**
- REF: +0.061R. SECOND: +0.005R.

## 1. Universe: every market in the dataset (152)
The same unchanged dip-buy rules are run on each market: RSI(2) < 10 above SMA200, exit RSI(2) > 70 / 10 days / 3×ATR stop. Costs:

| Group | Markets | Costs (bps spread / slip / swap per night) |
|---|---|---|
| PRIMARY, SECOND, REF | as pre-registered in `validate_mr.py` | unchanged |
| STOCKS | 88 single stocks (US, EU, UK, CH, JP) + GOOG | 6 / 1.5 / 2 (as `frequency_mr.py`) |
| FX | EURUSD GBPUSD USDJPY AUDUSD NZDUSD USDCAD USDCHF USDCNH USDSEK + dollar index | 1.0 / 0.3 / 0.5 (CNH, SEK: 3 / 1 / 0.5) |
| COMMOD | Brent, WTI, gas, copper, cocoa, coffee, cotton, OJ, soybeans, sugar, diesel, palladium, platinum | 3 / 1 / 2 |
| CRYPTO | BTC, ETH, BITO | 10 / 3 / 5 |
| OTHER | VXX, IGLN | 3 / 1 / 2 |

- Long only, because the rules are unchanged; for FX "long" just follows the quote convention.
- Nothing is excluded for being thin or noisy: VXX and BITO are included and flagged.

## 2. The fear filter: a vote, never a trade generator
For every candidate trade the baseline produces (signal at day D's close, entry at D+1's open), the filter votes **VETO** or **PASS** using only information known at D's close. Five components; a missing input never votes VETO.

| # | Component | Fear state = VETO vote | Source / coverage | Timing |
|---|---|---|---|---|
| F1 | VIX term structure | VIX/VIX3M ≥ 1.00 (backwardation). Before VIX3M exists (pre-2009-09): **proxy** VIX ≥ 1.3 × its 60-day mean | CBOE VIX 1990+, VIX3M 2009+ | close D (US close is before the UTC daily close) |
| F2 | Put/call on low volume | Equity put/call 10-day mean ≥ 80th percentile of its trailing 252 days **and** equity option volume 10-day mean < its trailing-252 median | CBOE equitypc, **2006-11 → 2019-10 only** | lagged 1 day |
| F3 | Dealer gamma regime | GEX < 0 (dealers short gamma) | SqueezeMetrics GEX, 2011-05+ (a model estimate, not exchange data) | lagged 1 day |
| F4 | Breadth pressure (built from our price data) | ≥ 60% of equity markets with data that day (indices + equity ETFs) close below their SMA50 | Dukascopy daily | close D |
| F5 | Order-flow **proxy**: no confirming candle | The signal-day candle closes in the bottom 25% of its range (selling into the close) | Dukascopy daily | close D |

- **F5 limitation:** this data has **no bid/ask (order-flow) volume**. F5 is a candle-shape proxy, not order flow.
- F1–F4 are market-wide (US-centred) fear gauges, applied to every market as a global risk-off flag. F5 is per market.

**Primary filter (the one judged):** VETO when **≥ 2 of the 5** components are in fear.
- Each component alone, and the inverse ("trade only in fear"), are reported as diagnostics. They are never selected.

## 3. Outputs
- Expectancy (net R), win %, PF, and trades kept vs vetoed. Shown per market, per group, per year and per VIX regime at entry (VIX < 15, 15–25, > 25).
- **The single number:** annualised Sharpe of the daily portfolio P&L, all 152 markets at 0.5% risk per trade, P&L booked on the exit day. Filter on vs off.
  - Significance by **random veto** (1,000 runs): veto the same number of trades at random, which also tests the trade-count effect. p = share of random runs with Sharpe ≥ the filtered one.
  - **"Good years look better" test:** the Sharpe/expectancy difference year by year. The filter only counts as improving risk-adjusted return if it helps in more years than it hurts **and** the gain isn't concentrated in 2023–26.

## 4. Stress tests (in this order)
1. **Winner purge:** remove the top 5% of trades by R, recompute expectancy (filter off and on; per group and overall). If it turns negative, the edge comes from outlier fills.
2. **Crisis windows (fixed list):**
   - 2008-09-01→2009-03-31 (only FX, gold and silver have data: equities start 2011–13 in this dataset)
   - 2011-07-25→2011-10-31
   - 2015-08-10→2015-09-30
   - 2018-01-26→2018-04-30
   - 2018-10-01→2018-12-31
   - 2020-02-19→2020-04-30
   - 2022-01-03→2022-10-31
   - 2024-07-15→2024-08-31
   - 2025-02-19→2025-04-30
   - **Plus every data-defined breakdown:** an episode where the rolling 30-trade expectancy of PRIMARY+REF falls below −0.30R.
   - Measured per window: trades, cumulative R, worst drawdown in R, days from the window start to the trough, number of stop-outs, average stop-out R (≈ −1.0 means the stops held; below −1.1 means gaps went through them), and the worst trade. Filter off vs on.
3. **Cross-instrument:** edge and filter effect per group: US equity / non-US equity indices (incl. FTSE) / single stocks / FX / commodities / metals / bonds / crypto. An edge that lives only in US equities is "beta in a costume".

## 5. Other strategies
The same filter, by date, is applied to the saved trade logs of every other strategy tested:
- VWAP A/B/C (intraday; filter state at D−1's close);
- hourly dip-buy H-A/H-B;
- ATR-EW walk-forward A/B.

Expectancy is reported filter on vs off.

## 6. Verdict rule (fixed now)
- **ADOPT** only if the filter raises the full-dataset portfolio Sharpe after costs, beats random veto (p < 0.05), helps in more years than it hurts, and does not lower expectancy in the PRIMARY group's 2023–26 holdout.
- **REJECT** if Sharpe doesn't improve or doesn't beat random veto.
- **ITERATE** if it improves Sharpe but fails the year-consistency or random-veto test only narrowly (0.05 ≤ p < 0.15).
