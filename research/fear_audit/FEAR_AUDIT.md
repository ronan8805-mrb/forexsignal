# Fear-filter audit of the daily dip-buy: **verdict REJECT**

> **Reason:** its Sharpe gain (0.404 → 0.478) is indistinguishable from luck. It fails the random-veto test (p = 0.052 over 100,000 GPU runs; 0.165 when vetoes cluster by date like real fear days), its bootstrap 95% interval spans zero, and it hurts in 12 of 23 years: more than all of its net gain sits in 2023–26.

- **Read-only audit.** No existing code, config, strategy file or the live engine was changed. The original `validate_mr.py` and `robustness.py` md5 sums were verified unchanged after use. All new files are in `research/fear_audit/`.
- The rules were fixed in `FEAR_PREREG.md` before any filtered result was seen. Anything marked *post-hoc* below is a diagnostic added afterwards and never used to choose the filter.

## 0. Baseline reproduced exactly
`validate_mr.py` was re-run unchanged and matches REPORT §9 to the last digit, including the random-entry control p-values.

| Group | Trades | Win % | Net exp. | PF | Random C1 / C2 p | 2013–22 | **2023–26 holdout** |
|---|---|---|---|---|---|---|---|
| PRIMARY (21 unseen equity) | 1,307 | 71.0 | **+0.043R** | 1.23 | 0.005 / 0.014 | +0.008R, PF 1.03 | **+0.082R, PF 1.59** |
| REF (development 5) | 557 | 70.7 | +0.061R | 1.37 | 0.004 / 0.016 | +0.045R | +0.092R |
| SECOND (non-equity 9) | 365 | 61.6 | +0.005R | 1.03 | 0.21 / 0.22 | −0.018R | +0.033R |

**The baseline itself leans on 2023–26:** 2013–22 was close to breakeven for PRIMARY.

The audit then ran the same unchanged rules on **all 152 markets** in the dataset (8,957 trades):
- the 35 original markets and 88 single stocks;
- 10 FX series: 9 pairs + the dollar index;
- 12 energy/agricultural, 8 metals, 3 crypto and VXX.

## 1. The filter (votes on trades, never creates them)
| # | Input | Fear = veto vote | Coverage of candidate trades |
|---|---|---|---|
| F1 | VIX term structure (CBOE VIX/VIX3M ≥ 1; VIX ≥ 1.3 × its 60-day mean before 2009) | backwardation | 100% (97% real VIX3M) |
| F2 | Equity put/call on low volume (CBOE) | P/C 10-day mean in the top 20% of the trailing year **and** option volume below its yearly median | **27%: CBOE's file ends Oct 2019, so there is none for the 2023–26 holdout** |
| F3 | Dealer gamma (SqueezeMetrics GEX, a model estimate) | GEX < 0 | 96% |
| F4 | Breadth, built from our data | ≥ 60% of equity markets below their SMA50 | 96% |
| F5 | **Order-flow proxy**: no confirming candle | signal day closes in the bottom 25% of its range | 100% |

- **Limitation:** the data has **no bid/ask volume**. F5 is a candle-shape stand-in, not order flow.
- **Composite rule:** veto if ≥ 2 of the 5 are in fear. It vetoes 21.9% of trades.

## 2. The single number: risk-adjusted return after costs, full dataset
| | Filter OFF | Filter ON | Random-veto control |
|---|---|---|---|
| Portfolio Sharpe, all 152 markets (0.5% risk/trade) | **0.404** | **0.478** (+0.074) | pre-registered per-trade shuffle: p = **0.052** (100,000 runs on the RTX 5090; 1,000 CPU runs gave 0.040). *Post-hoc* date-clustered: p = **0.165** |
| Sharpe gain, 20-day block bootstrap (100,000 resamples, GPU) | | | 95% CI **−0.26 to +0.44**; P(gain ≤ 0) = **0.37** |
| PRIMARY only | 0.469 | 0.576 | |
| PRIMARY + REF | 0.495 | 0.554 | |
| ETFs the live engine trades now | 0.731 | 0.754 | |

**Does it improve risk-adjusted return, or just make the good years look better? The latter.**
- **All markets:** better Sharpe in **8** years, worse in **12**, no change in 3.
  - The 2023–26 years contribute **+1.30** Sharpe-points against a net **+1.23** across all years, so the 19 years before 2023 net to −0.07.
- **PRIMARY+REF:** helps in 6 years, hurts in 12.
- **Big wins:** 2020 (−0.38 → +0.92), 2023, 2025 and 2026.
- **Big hurts:** 2018 (−0.76 → −1.38), 2019 (+0.19 → −0.37) and 2024 (+0.61 → −0.40).
- **Why the date-clustered control matters:** real fear vetoes land on whole days at once. The per-trade shuffle ignores that, which makes the evidence look stronger than it is. Clustered by date, the filter sits inside the random range (95th percentile 0.555 against the filter's 0.478).
- **The GPU controls settle it:** with 100,000 runs, even the pre-registered per-trade test misses p < 0.05. The bootstrap says the gain could just as well be zero.

## 3. Expectancy, filter off → on (net R)
| Slice | Off | On | Vetoed trades |
|---|---|---|---|
| All 152 markets | +0.0228 | +0.0262 | +0.0104 |
| PRIMARY 2023–26 holdout | +0.0823 | +0.1015 | +0.0099 |
| PRIMARY 2013–22 | +0.0075 | +0.0157 | −0.0220 |
| VIX < 15 | −0.0051 | −0.0047 | 53 trades vetoed |
| VIX 15–25 | +0.0375 | +0.0378 | +0.0366 |
| VIX > 25 | +0.0156 | **+0.0787** | −0.0289 |

- **Per market:** the filter raises expectancy in 86 of 152 and lowers it in 65 (`out_per_instrument.csv`). That is close to a coin flip.
- **The only regime where it clearly helps is VIX > 25.** It removes 59% of those trades, and the removed ones averaged −0.029R.
- **Single components (diagnostic):**
  - VIX backwardation, negative gamma and weak breadth each mark weaker dip-buys (+0.006 to +0.008R, against +0.023 to +0.028R otherwise).
  - **The candle proxy F5 points the wrong way:** dips that close near the low did *better* (+0.026R vs +0.018R).

## 4. Stress 1: winner purge (drop the top 5% of trades)
| Set | Exp. | After purge | Purge, filter on |
|---|---|---|---|
| All 152 markets | +0.023R | **−0.021R** | −0.015R |
| PRIMARY | +0.043R | **+0.004R** | +0.018R |
| US equity indices / ETFs | +0.079R | **+0.034R** | +0.045R |
| FX | +0.001R | −0.041R | −0.034R |
| Single stocks US / EU / JP | +0.032 / +0.009 / +0.033R | −0.014 / −0.033 / −0.011R | |

- **Across the full dataset the edge is the outliers:** the top 5% of trades (≥ +0.62R) supply 186% of the total R.
- **PRIMARY** keeps almost nothing (+0.004R).
- **Only US equity indices/ETFs keep a clear positive expectancy after the purge.**
- **Largest trade, +8.8R:** USDCNH on 11 Aug 2015, China's yuan devaluation. It is real: a near-pegged pair with a tiny ATR.
- **Data artifact (flagged, not fixed):** Dukascopy US500/NAS100 has no data from 22 Feb to 22 May 2013. Two REF "3-day" trades span that gap for +7.5R, which lifts REF from +0.047R to +0.061R. PRIMARY is unaffected.

## 5. Stress 2: crisis windows (how fast it dies; do the stops hold?)
Equity core = PRIMARY + REF, 0.5% risk per trade.

| Window | Trades | Cum. R | Per trade | Days to trough | Stop-outs, avg R | Worst | Filter on: cum. R |
|---|---|---|---|---|---|---|---|
| Aug 2011 | 4 | −1.3 | −0.33 | 60 | 2, −1.01 | −1.01 | −1.0 |
| Aug 2015 China | 2 | −2.0 | −1.01 | 11 | 2, −1.01 | −1.02 | 0 (all vetoed) |
| Feb 2018 Volmageddon | 48 | −11.3 | −0.24 | 56 | 17, −1.04 | −1.33 | −7.2 |
| Q4 2018 | 20 | −9.1 | −0.46 | 10 | 11, −1.03 | −1.11 | −8.2 |
| **COVID 2020** | 19 | **−18.5** | **−0.97** | **26** | 17, −1.08 | −1.41 | **−4.0** |
| 2022 bear | 47 | −18.0 | −0.38 | **266** | 22, −1.03 | −1.21 | −12.1 |
| Aug 2024 yen carry | 34 | **+0.9** | +0.03 | 10 | 7, −1.03 | −1.05 | **−5.1** |
| 2025 tariffs | 31 | −9.5 | −0.31 | 47 | 12, −1.11 | −1.53 | −4.9 |

- **How it dies:**
  - Fast crashes kill it in 2–8 weeks. In COVID nearly every trade stopped out.
  - The 2022 bear was a slow nine-month bleed.
- **Stops:**
  - On indices and ETFs they held: average stop-out −1.03 to −1.11R, worst −1.53R.
  - On **single stocks**, earnings or news gaps went straight through them: −2.9R GSK, −2.7R CRM, −2.5R SAP.
- **The filter helps in grinding or panicky declines** (COVID, 2022, 2025) **and hurts in sharp V-shaped shocks** (Aug 2024; Feb 2018 on the full dataset). Those are exactly the dips the strategy exists to buy.
- **2008:** the dataset has **no equity data before 2011–13**. Only FX, gold and silver traded the GFC window (26 trades, −0.3R). The filter made it worse (−0.8R).
- **Data-defined breakdowns** (rolling 30-trade PRIMARY+REF < −0.30R): 7 episodes, late 2017 to 2025. Details in `out_crisis_windows.csv`.

## 6. Stress 3: cross-instrument (beta in a costume?)
| Asset class | Markets | Net exp. (off → on) | Markets positive | Gross exp. |
|---|---|---|---|---|
| US equity indices/ETFs | 17 | **+0.079 → +0.091R** | 15/17 | +0.111R |
| Single stocks JP | 10 | +0.033 → +0.043R | 7/10 | +0.068R |
| Single stocks US | 49 | +0.032 → +0.034R | 30/49 | +0.063R |
| Non-US equity indices/ETFs (incl. FTSE −0.051R) | 7 | +0.020 → +0.033R | 3/7 | +0.052R |
| Single stocks EU/UK/CH | 30 | +0.009 → +0.009R | 15/30 | +0.047R |
| **FX** (10) | 10 | **+0.001 → −0.000R** | 6/10 | +0.020R |
| Metals / energy & ags / bonds / crypto | 8 / 12 / 5 / 3 | −0.009 / −0.006 / −0.023 / −0.018R | ≈ half | +0.007 to +0.044R |

- **The edge lives in equities, mostly US equity indices and ETFs, and dies on FX, commodities, bonds, crypto and the FTSE.** By your test, that is a costume.
- **Nuance:** the reproduced random-entry control shows the dip timing beats random long entries on the same uptrend days (p = 0.005). So it is an equity-specific mean-reversion effect rather than pure beta, but it does not generalise beyond equities.
- **The filter doesn't change which classes work.**

## 7. Every other strategy, filter applied by date (F1–F4, state as of the previous close)
| Strategy | Exp. off | Exp. on |
|---|---|---|
| VWAP pullback / reclaim / band fade | −0.154 / −0.154 / −0.105R | −0.156 / −0.160 / −0.103R |
| Hourly dip-buy H-A / H-B | −0.043 / −0.043R | −0.044 / −0.045R |
| ATR-EW walk-forward A / B | −0.104 / −0.114R | −0.106 / −0.117R |
| Daily mean-reversion development set | +0.049R | +0.038R |

**The filter rescues nothing,** and on the losing strategies the vetoed trades were mostly *less* bad than the kept ones.

*Post-hoc diagnostic:* dropping the candle proxy F5 (≥ 1 or ≥ 2 of F1–F4) gives the same Sharpe (0.477 / 0.472). F5 is not what drives the result.

## 8. Verdict against the pre-registered rule
| Criterion | Result |
|---|---|
| Raises full-dataset Sharpe after costs | ✅ 0.404 → 0.478 |
| Beats random veto, p < 0.05 | ❌ 0.052 pre-registered (per-trade, 100,000 GPU runs; the 1,000-run estimate of 0.040 was noise); ❌ 0.165 date-clustered (post-hoc, the fairer null) |
| Helps in more years than it hurts | ❌ 8 helps vs 12 hurts (PRIMARY+REF: 6 vs 12) |
| Doesn't lower PRIMARY 2023–26 holdout expectancy | ✅ +0.082 → +0.102R |

**Not ADOPT.** The random-veto p (0.052) sits just inside the ITERATE band, but year consistency fails clearly (8 vs 12) and the bootstrap interval spans zero. The gain is a reshuffle that flatters 2020 and 2023–26, not a consistent improvement, so **REJECT**.

What survives, for any future work:
- VIX backwardation, negative dealer gamma and weak breadth do mark weaker dip-buys, and the filter clearly helps when VIX > 25.
- Testing that would need a *new* pre-registration judged on future data, not a tweak to this result.

## 9. Independent verification
- `audit.py` was re-run on the user's PC (Python 3.14): identical 8,957 trades and identical headline numbers. `validate_mr.py` was confirmed unchanged.
- A separate agent re-derived the fear flags from the raw CBOE and SqueezeMetrics files and recomputed the headline numbers. Result below.

## Files (all new, in `research/fear_audit/`)
- **Rules and code:** `FEAR_PREREG.md` (rules, fixed first) · `audit.py` (main run) · `extra.py` (diagnostics) · `gpu_controls.py` (100,000-run controls + bootstrap on the RTX 5090; output `out_gpu_controls.csv`) · `resample_all.py` (daily bars for the 29 markets not resampled before)
- **Reproduction:** `repro/` (baseline re-run + md5 check)
- **Inputs:** `data/` (CBOE VIX, VIX3M, VIX9D, put/call; SqueezeMetrics DIX/GEX) · `data_daily_extra/`
- **Results:** `out_trades_all.csv` (every trade with its fear flags) · `out_per_instrument.csv` · `out_by_year.csv` · `out_crisis_windows.csv` · `out_audit_log.txt` · `out_extra_log.txt`
