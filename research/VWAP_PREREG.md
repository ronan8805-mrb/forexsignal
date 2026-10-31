# VWAP intraday setups: pre-registered test (written 2026-10-31, before any results)

Rules fixed before testing. Nothing below may be changed after seeing results. Neighbouring values are reported as a plateau check, never selected.

## Data and sessions
Dukascopy M1 **bid** bars with Dukascopy volume (a tick-volume proxy, the same kind of volume an MT5 CFD feed gives).

| Market | Session (local exchange time) | Data from |
|---|---|---|
| US500, NAS100 | 09:30–16:00 New York | 2011-09 |
| US30 | 09:30–16:00 New York | 2013-09 |
| DE40 | 09:00–17:30 Frankfurt | 2013-09 |
| UK100 | 08:00–16:30 London | 2011-09 |
| JP225 | 09:00–15:00 Tokyo | 2011-09 |
| XAUUSD | 08:00–17:00 London (London + New York overlap) | 2006-10 |

- VWAP is anchored at the session open. Every position is flat by the session close.
- Days with < 80% of the session's minutes are dropped.

## Indicators
- `tp = (H+L+C)/3` per M1 bar.
- `VWAP = Σ v·tp / Σ v` from the session open.
- `σ = sqrt(Σ v·tp² / Σ v − VWAP²)`: the band unit. Equal weights if the volume is zero so far.
- Signals are judged on **M5 bars** aligned to the session open. VWAP and σ are taken at the M5 bar close.
- `ATR5` = Wilder ATR(14) of M5 bars. The first bar of each session uses H−L (no overnight gap).
- `slope30 = VWAP(t) − VWAP(t−6 bars)`, `slope60 = VWAP(t) − VWAP(t−12 bars)`.
- **"Order flow turns" proxy** (no bid/ask volume exists in this data). Long: the bar closes up (C > O) in the top 40% of its range, C ≥ L + 0.6(H−L). Short: the mirror image.

## Timing and position rules
- No signals in the first 30 minutes (setup C: 60 minutes). No new entries in the last 60 minutes.
- Entry at the open of the next M1 bar after the M5 signal bar closes.
- One open position per market per setup, and at most 3 entries per setup per market per day.
- Stops are checked on M1 bars. If a stop and a target fall in the same M1 bar, the stop counts first. A gap through the stop fills at the bar open.

## Setup A: trend pullback (long; short is the exact mirror)
1. `slope30 > 0`, and all 6 previous M5 closes are above VWAP.
2. The high of those 6 bars reached ≥ VWAP + 1.0·ATR5: price had moved away.
3. Pullback: this bar's low ≤ VWAP + 0.2·ATR5.
4. Holds: close > VWAP.
5. Confirmation candle (long).
- Stop = min(low, VWAP) − 0.2·ATR5.
- Target = entry + 2R; otherwise exit at the session close.
- Trades with R < 0.25·ATR5 or R > 3·ATR5 are skipped.

## Setup B: reclaim / rejection (long reclaim; short rejection is the mirror)
1. Cross-up bar k in [t−6, t−2]: close(k−1) < VWAP and close(k) > VWAP.
2. Sellers had control: at least 4 of the 6 bars before k closed below VWAP.
3. Retest at bar t: low ≤ VWAP + 0.2·ATR5, close > VWAP, confirmation candle (long).
- Stop and target as in A.

## Setup C: band fade (short at the upper band; long at the lower band is the mirror)
1. Flat-VWAP range filter: |slope60| ≤ 0.5·ATR5. Trend days are skipped by this.
2. Short: high ≥ VWAP + 2σ and close < VWAP + 2σ (touch and close back inside), with a bearish confirmation candle.
- Stop = high + 1.0·ATR5.
- Target = the live VWAP (exit when an M1 bar trades back to the current VWAP); otherwise the session close.

## Costs (round trip, bps of price; half charged on entry, half on exit)
US500 1.5 · NAS100 1.2 · US30 1.0 · DE40 1.2 · UK100 1.5 · JP225 2.5 · XAUUSD 1.5.
That is about IC Markets raw spread + commission + 1 tick of slippage. Stress test: 2× costs.

## Evaluation
- Development: data start → 2022-12-31. **Holdout: 2023-01-01 → 2026-10-21.**
- Per setup, longs and shorts together:
  - trades/yr, win %, net expectancy (R), PF;
  - max drawdown at 0.5% risk per trade;
  - t-stat with trades grouped by day.
- **Random-entry control:** 200 runs. Each real trade is matched by a random entry with the same market, same direction, same period, same allowed time window, the same stop distance in ATR5 units and the same exit rule. p = share of runs whose net expectancy ≥ the real one.
- **Plateau check** (reported, not selected):
  - A/B: touch zone {0.1, 0.2, 0.35}·ATR5 × target {1.5R, 2R, 3R}.
  - C: band {1.5σ, 2σ, 2.5σ} × flat threshold {0.25, 0.5, 1.0}·ATR5.

## Pass criteria: a setup must meet all of them to go to demo
1. Net expectancy > 0 in development **and** in the holdout.
2. Holdout PF ≥ 1.2.
3. Beats random entries: p < 0.05 over the full period.
4. Net positive on ≥ 5 of the 7 markets over the full period.
5. Still net positive at 2× costs over the full period.
