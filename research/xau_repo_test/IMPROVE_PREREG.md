# Improving the gold EMA strategy: pre-registered plan (written 2026-10-31, before testing any variant)

**Diagnosis from the first test:**
- The repo's H1 rules have no entry-timing edge (p = 0.31 against random entries).
- Its fixed 3×ATR target and its RSI/EMA early exits cut trends short.
- Its 13–17 session filter was found by mining 2 years of data.
- On H1 with ~2 ATR stops, costs are a large share of each trade.

The improvements below address those points, in the spirit of the original: trend-following, an EMA-based trend filter, ATR risk.

## Data and splits
- Dukascopy XAUUSD H1 bid, Oct 2006 → Oct 2026. H4 and daily bars are built from it on the MT5 server clock (New York + 7 h).
- **Development 2006-10 → 2016-12** (choose here only) · **Validation 2017 → 2022** · **Holdout 2023 → 2026-10** (looked at once).

## Variants (all: one position at a time, entry at the NEXT bar's open, exits at the next open after an exit signal, stop orders filled at the stop or the gap open)
| | Idea | Entry | Initial stop | Exit |
|---|---|---|---|---|
| V0 | repo as fixed (reference) | as in RESULTS.md | | |
| V1 | let winners run | repo H1 signal (EMA9/21 cross + RSI 50–65/35–50 + H4 EMA50 trend + 13–17 session) | repo's (tighter of swing±0.5, 2×ATR) | 3×ATR(H1) trailing stop from the best close, or an opposite EMA cross; no TP, no RSI exit |
| V2 | slower timeframe, no mined session | EMA9/21 cross on **H4**, same RSI band, trend = last completed **daily** close vs daily EMA50, any hour | 2×ATR(H4) | 3×ATR(H4) trailing stop, or an opposite cross |
| V3 | daily trend-following (channel breakout) | daily close above the prior 50-day high (long) / below the 50-day low (short) | 2×ATR(daily, 20) | 3×ATR trailing stop, or a close beyond the opposite 20-day channel |
| V4 | V3, long only | V3 longs only | as V3 | as V3 |

**Costs (round trip, bps of price):** spread 1.5 + commission 0.35 + slippage 0.5, plus financing 1.5 bps per night held. Stress test: 2× all costs.

## Selection rule (fixed now)
1. **Pick** the variant with the highest development expectancy (net R per trade) among those with ≥ 50 development trades.
2. **Validate:** it must be positive on 2017–22, or the result is "no improvement found".
3. **Holdout:** report 2023–26 once.
4. **Random-entry control:** 300 runs with the same timeframe, same direction mix and the same stop and exit rules. p = share of runs with expectancy ≥ the real one, over the full period.

**A variant passes only if all of these hold:**
- positive on validation **and** holdout;
- holdout PF ≥ 1.2;
- p < 0.05 against random entries;
- still positive at 2× costs.

**Caveat noted in advance:** 2023–26 was one of gold's strongest bull runs (~$1,800 → $4,100). Long-biased trend rules will look good there, which is why the random-entry control and the long/short split matter.
