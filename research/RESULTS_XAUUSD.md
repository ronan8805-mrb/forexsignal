# ATR-EW backtest on XAUUSD: 20 years of Dukascopy data

**Data:** Dukascopy XAUUSD bid, 1-minute bars, 2006-10-22 → 2026-10-20 (7.1M rows, audited clean).
The 1-minute bars were resampled to M15 (481k bars) and H1 (121k bars) by `resample.py`.

**Engine:** `atr_ew_backtest.py`, a bar-by-bar Python port of `pine/atr_ew_hybrid.pine`.
- Entries fill at the next bar's open.
- If a bar hits both the stop and a target, the stop is assumed first (conservative).
- Partial exits are 40/30/30, with the stop moved to break-even after TP1 and to TP1 after TP2.
- Costs: spread 0.30 USD/oz (includes commission), plus 0.05 slippage on entries and stops.
- Results are in **R** (multiples of the initial risk), so position size doesn't affect them.

## 1. Default settings (exactly as shipped in the Pine script)

| | Trades | Trades/yr | Win % | Avg R (net) | Total R | PF (net) | PF (no costs) |
|---|---|---|---|---|---|---|---|
| **H1** | 540 | 27 | 44.8 | −0.024 | −12.9 | 0.96 | 1.04 |
| **M15** | 1721 | 86 | 40.1 | −0.181 | −310.9 | 0.70 | 0.83 |

- **M15 loses in 18 of 20 years, even before costs.** It has no edge on gold.
- **H1 is roughly breakeven before costs and slightly negative after.** It has no edge either.
- This matches what TradingView showed on EURUSD.

## 2. Robustness sweep (`sweep.py`)

The sweep tested 96 settings on H1. Settings were **chosen on 2007–2018** and **checked on 2019–2026**.

- Rank correlation between the in-sample and out-of-sample results: **0.02**. Being good in-sample did **not** predict being good out-of-sample, so most differences between settings are noise.
- 69 of the 96 settings were positive in 2019–2026. That period was a strong gold bull market, which mostly helped long trades.

## 3. One candidate worth forward-testing (not proven)

The settings were `trig_frac=0.5`, `min_swing_atr=1.5`, `sl_mode=tighter`, `w3_trend_req=True` (trade wave 3 only in the direction of the EMA200), `min_adx=25`, with everything else at default.

| | Trades | Avg R | PF | Notes |
|---|---|---|---|---|
| 2007–2018 (in-sample) | 194 | +0.107 | 1.15 | long and short similar |
| 2019–2026 (out-of-sample) | 107 | +0.355 | 1.55 | longs +0.63R, **shorts −0.07R** |
| Full 20 years, spread 0.30 | 301 | +0.195 | 1.29 | max drawdown 20R |
| Full 20 years, spread 0.80 | 301 | +0.124 | 1.18 | still positive with wider costs |

- The **W3 setup produced the edge** (+0.26R). The **ABC setup added nothing** (−0.02R).
- It makes only about **15 trades per year** on gold H1, far from 5–6 signals per day.

**Caveats:**
- It was picked from 96 tries, so part of its result is luck from that selection.
- Its strong recent years come from long trades during a record gold rally.

Treat it as a hypothesis. Before any real money:
1. Run it **unchanged** on other markets (EURUSD, GBPUSD, USDJPY).
2. Forward-test it on a **demo account** for at least a few months.

## 4. Cross-market check: 8 other markets, 20 years, settings unchanged (`multi_market.py`)

Data: Dukascopy bid M1 2006–2026 for XAGUSD, EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF and USDJPY. Each market uses its own typical spread and slippage.

| Market | Default M15 avgR | Default H1 avgR | Candidate H1 avgR (trades) |
|---|---|---|---|
| XAUUSD (tuned here) | −0.181 | −0.024 | **+0.195** (301) |
| XAGUSD | −0.410 | −0.327 | −0.265 (314) |
| EURUSD | −0.150 | −0.162 | −0.089 (288) |
| GBPUSD | −0.170 | −0.149 | +0.019 (288) |
| AUDUSD | −0.162 | −0.132 | +0.131 (271) |
| NZDUSD | −0.255 | −0.094 | +0.019 (324) |
| USDCAD | −0.224 | −0.134 | +0.031 (224) |
| USDCHF | −0.213 | −0.076 | −0.118 (274) |
| USDJPY | −0.237 | −0.130 | −0.093 (297) |

- **Default settings: negative on all 9 markets and both timeframes, after costs.** Most are negative even before costs.
- **The candidate on the 8 markets it was not tuned on:** 4 of 8 positive, 4 negative. Pooled over 2,280 trades it averages **−0.05R**. The gold result did **not** carry over; it was most likely selection luck plus gold's long bull run.
- **Conclusion: the ATR + Elliott Wave rules as designed show no tradable edge.** The infrastructure (bridge, Telegram, EA, backtester) is sound and reusable for a different signal idea.

Full table: `multi_market_results.csv`.

## Reproduce

```
python research/resample.py <dukascopy_m1.csv> research/data
python research/atr_ew_backtest.py research/data/xauusd_m60.csv
python research/sweep.py research/data/xauusd_m60.csv
```
