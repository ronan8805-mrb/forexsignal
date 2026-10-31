# Strategy validation report: ATR + Elliott Wave hybrid

*Data: Dukascopy, 2006–2026. Method: walk-forward with an untouched final holdout. All figures are net of costs unless marked "gross".*

## 0. Verdict

| Objective | Result |
|---|---|
| Win rate ≥ 70% | **Reachable** (73% out-of-sample), but only with a 0.3R target |
| Expectancy > 0 | **No.** −0.10R per trade out-of-sample, −0.12R on the holdout |
| Profit factor ≥ 1.5 | **No.** 0.60–0.64 |
| Max drawdown < 20–25% | **No.** Monte Carlo median about 88% at 0.5% risk per trade |
| Works across instruments and regimes | **It loses on all 14 instruments, in every year 2013–2026 and in every regime** |

**The 70%-win-rate configuration is rejected, as the brief requires.**
- It reaches 73% wins by taking +0.24R winners against −1.02R losers.
- The underlying entries have **no predictive information** (Section 2), so no exit rule can turn them into a positive expectancy.
- All 40 variants were negative over the full history, **even with zero costs**.

**Best robust alternative tested** (Section 5): daily RSI(2) dip-buying on equity indices and gold, long only.
- Out-of-sample: 66% wins, +0.055R per trade, PF 1.20, max drawdown 4%, 291 trades.
- It is **not statistically significant** (t = 1.34).
- It turns breakeven at 3× costs and negative with a 1-day entry delay.
- ~~A random-entry control did as well or better~~ **Corrected in Section 9:** that early control was flawed. A proper 1,000-run control on 21 unseen equity instruments shows the dip timing **does** beat random uptrend entries (p ≈ 0.005–0.014).

It **does not meet the brief's primary target**. It is at most a candidate for paper trading, not for live capital.

---

## 1. Data and assumptions

**Instruments (1-hour bars built from Dukascopy 1-minute bid data):**

| Class | Instruments | History |
|---|---|---|
| FX | EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY | 2006-10 → 2026-10 (NZDUSD from 2006-01) |
| Metals | XAUUSD, XAGUSD | 2006-10 → 2026-10 |
| Indices | US500, US Tech 100, DE40 | 2011-09 → 2026-10 (DE40 from 2013-10) |
| Crypto | BTCUSD (from 2017-05), ETHUSD (from 2017-12) | shorter history; weighted accordingly |

- **Quality:** the XAUUSD 1-minute source passed its schema audit (7.1M rows, no nulls, duplicates or bad OHLC). Gaps are weekends and holidays and were **never forward-filled**. The other instruments come from the same exporter. Daily bars with fewer than 12 hourly bars were dropped.
- **Not tested:** EURGBP, EURJPY and GBPJPY (no native series in the library; synthetic crosses from bid×bid would understate spreads), and SOL (no history).

**Costs (`universe.py`):**
- **Spread including commission:**
  - FX: 1.0–1.8 pips
  - XAU: $0.30
  - XAG: $0.025
  - Indices: 1.5 basis points of price
  - BTC: 5 bps; ETH: 8 bps
- **Slippage:** applied on entries and stops.
- **Overnight financing:** charged per 22:00 UTC rollover, regardless of direction (conservative):
  - FX: 0.3 bps
  - Metals: 1.5 bps
  - Indices: 2 bps
  - Crypto: 5.5 bps

**Execution model:**
- Signal on bar close, fill at the next bar's open.
- Longs pay the ask; exits are on the correct side of the spread.
- If a bar touches both the stop and a target, **the stop is assumed first**.
- Gaps through the stop fill at the open.
- One position per instrument at a time.

**No look-ahead:**
- Pivots are only used R bars after they form.
- Indicators use closed bars only.
- Parameter selection uses training data only.
- The 2023–2026 holdout was evaluated once, at the end.

---

## 2. Baseline and the entry-edge diagnostic

Default Pine settings, 1-hour bars, full history (`edge_diagnostic.py`, `out_baseline.csv`):

| | Trades | Win % | Expectancy (net) | Expectancy (gross) |
|---|---|---|---|---|
| Pooled, 14 instruments | 6,351 | ≈ 41 | **−0.150R** | −0.067R |

- 13 of 14 instruments are negative after costs; only ETH is slightly positive (+0.01R).
- On 15-minute bars (9 instruments, run earlier) it is worse: −0.15R to −0.41R on every instrument.

**Do the entries predict price?** For every signal, the favourable and adverse moves over the next N bars were measured in ATR, from the actual entry price. The same measurement was taken for 20× as many random entries (same instruments, same session hours, random direction).

| Horizon | Favourable ÷ adverse move: signals | Random | Price ended in trade direction: signals | Random |
|---|---|---|---|---|
| 5 bars | 1.009 | 1.003 | 48.5% | 49.9% |
| 10 bars | 1.012 | 1.004 | 48.7% | 49.8% |
| 20 bars | 0.999 | 1.000 | 48.2% | 50.1% |

**The entries are statistically indistinguishable from random**, and their direction is slightly worse than a coin flip. This is the root cause of everything that follows.

---

## 3. Walk-forward optimisation (`walkforward.py`)

**Free parameters (4)**, identical for every instrument:
- Single take-profit at {0.3, 0.5, 0.7, 1.0}R, or the original 3-step ladder
- Time stop: off or 24 bars
- Minimum ADX: 18 or 25
- Trigger fraction: 0.5 or 1.0

That gives **40 variants, all logged** (`out_grid_trades.pkl`, `out_grid_variants.csv`).

**Windows:** rolling 6-year train / 2-year test, giving 5 out-of-sample windows (2013–2022), plus an **untouched 2023–2026 holdout** (6 out-of-sample windows in total).

**Selection tracks:**
- **A** = best training expectancy with training win rate ≥ 70%
- **B** = best training expectancy, unconstrained

| Window | A: chosen | A: training win / expectancy | A: out-of-sample win / expectancy / PF | B: out-of-sample win / expectancy / PF |
|---|---|---|---|---|
| 2013–14 | TP 0.3R, time 24, ADX 18, trig 0.5 | 73.7% / −0.077 | 72.5% / −0.115 / 0.59 | 72.5% / −0.115 / 0.59 |
| 2015–16 | TP 0.3R, ADX 25, trig 0.5 | 74.4% / −0.077 | 73.0% / −0.091 / 0.67 | 63.5% / −0.098 / 0.74 |
| 2017–18 | TP 0.3R, ADX 25, trig 0.5 | 73.8% / −0.088 | 75.5% / −0.070 / 0.72 | 66.8% / −0.064 / 0.81 |
| 2019–20 | TP 0.3R, ADX 25, trig 0.5 | 74.3% / −0.085 | 73.0% / −0.099 / 0.64 | 63.7% / −0.105 / 0.72 |
| 2021–22 | TP 0.3R, ADX 25, trig 1.0 | 74.3% / −0.081 | 71.4% / −0.115 / 0.61 | 59.2% / −0.157 / 0.62 |
| **Holdout 2023–26** | TP 0.3R, ADX 25, trig 0.5 | 73.9% / −0.089 | **72.2% / −0.116 / 0.60** | 35.1% / −0.123 / 0.81 |

- **Even the best training setting is negative in every window**, so walk-forward efficiency is undefined. There is no positive in-sample result to carry forward.
- **Plateau check:** the neighbouring settings are just as negative (−0.08 to −0.16R). There is no positive region anywhere in the grid.

## 4. Robustness of the 70% configuration (Track A, `robustness.py A`)

| | Out-of-sample 2013–22 | Holdout 2023–26 |
|---|---|---|
| Trades | 2,868 | 1,098 |
| Win rate (95% CI) | **73.1% (71.4–74.7)** | **72.2% (69.5–74.8)** |
| Expectancy | −0.100R | −0.116R |
| Profit factor | 0.64 | 0.60 |
| Avg win / avg loss | +0.24R / −1.02R (payoff 0.24) | +0.24R / −1.03R |
| CAGR / max DD (0.5% risk) | −13.4% / 76.8% | −15.6% / 47.5% |
| Sharpe / Sortino / Calmar | −2.89 / −3.04 / −0.17 | −3.32 / −3.55 / −0.33 |
| Longest time under water | 3,629 days | 1,379 days |
| Positive months | 17.6% | 15.6% |

- **Monte Carlo (1,000 trade-order shuffles):** 100% of runs end in a loss. Median max drawdown 87.6%; P(drawdown ≥ 50%) = 100%.
- **Per instrument:** all 14 negative. The best is USDJPY at −0.022R; the worst is XAGUSD at −0.226R.
- **By year:** negative in all 14 years.
- **By regime:** negative in low, mid and high volatility, in trending, mixed and ranging markets, and through COVID 2020 and the 2022 rates shock.

**Stress tests (expectancy, PF):**

| Stress | Expectancy | PF |
|---|---|---|
| Zero costs | −0.051R | 0.81 |
| Costs ×2 | −0.182R | 0.43 |
| Entry delayed 1 bar | −0.193R | 0.57 |
| Best 5% of trades removed | −0.126R | 0.57 |

→ **Rejected.** This is the "tiny winners, large losers" pattern the brief warns about.

---

## 5. Best robust alternative: daily RSI(2) mean reversion (`meanrev.py`, `mr_robust.py`)

**The rules were fixed before testing.** They are public and well documented.

- **Entry:** buy the next open when close > SMA200 and RSI(2) < threshold. The short mirror was tested and reported separately.
- **Exit:** whichever comes first: an exit rule on a daily close, a stop at k × ATR(14), or 10 days.
- **3 free parameters:**
  - RSI(2) threshold: {5, 10, 15}
  - Exit rule: SMA5 / RSI > 50 / RSI > 70
  - Stop size: k = 2 or 3 ATR
- **Validation:** anchored walk-forward with test windows 2015–16, 17–18, 19–20, 21–22, plus the 2023–26 holdout.

| Scope | Track | Out-of-sample 2015–22 | Holdout 2023–26 |
|---|---|---|---|
| All 14, long + short | B | 63.3% win, +0.015R, PF 1.06 (599 trades) | 62.4%, +0.007R, PF 1.03 |
| All 14, long only | B | 62.9%, +0.014R, PF 1.05 | 65.3%, +0.035R, PF 1.12 |
| **Indices + metals, long only** | **B** | **64.6%, +0.032R, PF 1.11 (195 trades)** | **68.8%, +0.103R, PF 1.42 (96 trades)** |
| Indices + metals, long only | A (≥70% target) | 66.5%, +0.020R, PF 1.10 | 74.5%, +0.106R, PF 1.77 |

On the broad universe, results are essentially breakeven.

**Track A note:** no setting reached 70% wins in *training* (the best was 69.5%). The 74.5% holdout figure comes from 94 trades during a strong bull market. It is not evidence of a reliable 70% win rate.

**Indices + metals, long only, Track B, all 291 out-of-sample trades:**

| Metric | Value |
|---|---|
| Win rate (95% CI) | 66.0% (60.4–71.2) |
| Expectancy | +0.055R |
| Profit factor | 1.20 |
| Avg win / avg loss | +0.50R / −0.81R |
| Sharpe / Sortino / Calmar | 0.37 / 0.12 / 0.17 |
| Max DD (0.5% risk) | 4.0% |
| Positive months | 47.5% |
| t-statistic of mean R | **1.34 (not significant)** |

- **Plateau:** **all 18 settings are positive** over 2015–2026 (PF 1.06–1.46). That is a broad plateau, not a sharp peak, which is good.
- **By instrument:**
  - US500: +0.19R (PF 1.76)
  - US Tech: +0.07R
  - XAU: +0.04R
  - DE40: −0.02R
  - XAG: −0.10R
- **By regime:**
  - Positive in trending (+0.15R) and mixed markets.
  - **Negative in ranging (−0.10R), in high volatility (−0.13R) and through the COVID crash (−0.77R).**
- **Stress:**

| Stress | Expectancy | PF |
|---|---|---|
| Costs ×2 | +0.023R | 1.08 |
| Costs ×3 | −0.005R | 0.98 |
| Entry delayed 1 day | −0.016R | 0.94 |
| Best 5% of trades removed | −0.005R | 0.98 |

- **Control test (superseded):** a quick random-entry check here suggested the profit came from simply being long. **That check was flawed:** only one sample per instrument, and it entered on uptrend days with a broken frequency match. The proper control in Section 9 reverses that conclusion for equities.

**Assessment at this stage (before Section 9):** the alternative is a weak, regime-dependent long bias. It is not a robust edge, and it does **not** meet the primary target (≥70% win rate and PF ≥ 1.5).

---

## 6. Overfitting risk and residual failure modes

- **ATR-EW:** overfitting isn't the issue; there is nothing to overfit. Every setting loses before and after costs.
- **Alternative:**
  - Selection bias from 18 settings × 3 scopes × 2 tracks.
  - Low trade count (291 out-of-sample).
  - Dependence on a secular bull market in US indices and gold.
  - It fails in crashes, high volatility and ranging markets.
  - Its edge disappears with a 1-day delay or 3× costs.
- **Model limits:**
  - Stop-before-target ordering inside a bar is conservative but is not tick-accurate.
  - Constant spreads ignore widening around news.
  - The swap model is symmetric.
  - No funding modelling for crypto beyond the flat swap.

## 7. Instrument suitability

| Instrument | ATR-EW (any setting) | RSI(2) dip-buy, long |
|---|---|---|
| EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY | ✗ unsuitable | ✗ (≈ breakeven) |
| XAUUSD | ✗ | weak (+0.04R) |
| XAGUSD | ✗ (worst) | ✗ |
| US500 | ✗ | best (+0.19R, 74 trades) |
| US Tech 100 | ✗ | weak (+0.07R) |
| DE40 | ✗ | ✗ |
| BTC, ETH | ✗ | ✗ / insufficient |
| EURGBP, EURJPY, GBPJPY, SOL | not tested (no native data) | not tested |

## 8. Deployment recommendations

1. **Do not deploy the ATR-EW strategy with real capital,** at any win-rate setting. Keep the infrastructure (bridge, Telegram, EA, research harness); it is sound and reusable.
2. **If anything is forward-tested, use only the RSI(2) long-only dip-buy on US500, US Tech and gold, on a demo account:**
   - Fixed settings, chosen from the middle of the plateau rather than the peak: threshold 10, exit RSI > 70, stop 3 ATR.
   - At least **6 months or 50 trades** before any judgement.
   - Size at 0.25–0.5% risk per trade.
3. **Pause rules** (any one triggers a pause):
   - Rolling 50-trade expectancy < 0.
   - Drawdown > 2× the Monte Carlo 95th percentile (> 14%).
   - Any market where the price is below SMA200 (that market is excluded automatically).
4. **Monitor live vs backtest** for each trade: slippage, fill delay and spread. The backtest showed the edge is destroyed by a 1-day delay or 3× costs.
5. **A 5–6 signals/day objective is incompatible with every robust result found here.** Signal frequency should follow evidence, not the other way round.

## 9. Out-of-instrument validation of the RSI(2) dip-buy (`validate_mr.py`)

**Pre-registered.** The settings were fixed before this test:
- long only
- RSI(2) < 10 above SMA200
- exit when RSI(2) > 70
- 3 ATR stop, 10-day time stop

Instrument groups were also fixed before any result was seen.
- **Primary:** 21 equity instruments never used before (Dow, Nikkei, FTSE, Hang Seng, China A50, and 16 US equity ETFs).
- **Secondary:** 9 non-equity instruments, as contrast.
- **Reference:** the 5 development instruments.
- **Excluded:** VXX and BITO (futures products that lose value through roll costs).

**Data hygiene:** Dukascopy ETF CFDs are not split-adjusted. The 2:1 splits in XLK and XLE (Dec 2025) and the 1:3 reverse split in JNK (2019) were detected and back-adjusted. USO's 1:8 reverse split (Apr 2020) fell outside the tolerance, but no trade spanned it.

**Controls:** 1,000 runs of random entries on uptrend days. Each run uses one random draw per calendar day, shared by every instrument, so random entries cluster across correlated ETFs the way real dips do.
- C1 uses the same exit rules.
- C2 uses holding periods matched to the real trades, isolating entry timing.

| Group | Trades | Win % (95% CI) | Exp. (net) | PF | Max DD* | t (weekly-clustered) | Random C1 / C2 mean | p (timing ≤ random) |
|---|---|---|---|---|---|---|---|---|
| **Primary: 21 unseen equity** | 1,307 | **71.0 (68.5–73.4)** | **+0.043R** | **1.23** | 16.1% | 1.48 | +0.001 / −0.001R | **0.005 / 0.014** |
| Secondary: 9 non-equity | 365 | 61.6 | +0.005R | 1.03 | 5.2% | 0.18 | −0.011 / −0.015R | 0.21 / 0.22 |
| Reference: development 5 | 557 | 70.7 | +0.061R | 1.37 | 4.3% | 2.18 | +0.015 / +0.020R | 0.004 / 0.016 |

\* 0.5% risk per trade, all instruments traded at once (many overlapping, correlated positions).

**Primary group, detail:**
- **15 of 21 instruments are positive.**
  - Best: XLF +0.14R, IWM +0.12R, Dow +0.10R, Nikkei +0.09R, EFA +0.08R.
  - Negative: FTSE −0.05R, Hang Seng −0.03R, China A50, DIA, EEM and XLV (≈ 0).
- **Negative in 5 of 14 years:** 2014, 2018, 2019, 2020 and 2022.
- Average hold: 4.3 days. Average win +0.32R, average loss −0.64R.

**Cost sensitivity (primary group):**

| Cost model | Expectancy | PF |
|---|---|---|
| CFD (as tested) | +0.043R | 1.23 |
| Cash ETF or futures (1 bp spread + 0.5 bp slippage, no overnight financing) | +0.065R | 1.37 |
| Zero cost | +0.076R | 1.45 |

**What this establishes:**
1. **The dip timing is real on equities.** On 21 instruments it never saw, it beats random uptrend entries on the same price paths (p ≈ 0.01).
   - It shows **no** effect on bonds, oil, high yield or silver.
   - That matches the published finding that short-term mean reversion is an *equity-index* phenomenon. My earlier "it's just market drift" caveat was wrong for equities.
2. **The 70% win rate is genuine:** 71.0% on unseen instruments, with positive expectancy and without tiny-winner/huge-loser asymmetry (payoff 0.5).
3. **The edge is thin.** PF 1.23 net is below the brief's 1.5 target. Absolute profitability is **not** statistically significant once correlated trades are clustered (t = 1.48). It loses in about 1 year in 3, and costs take about 45% of the gross edge.

**Revised recommendation:**
- This is the only candidate in the study that survived out-of-sample, out-of-instrument testing.
- It does **not** meet PF ≥ 1.5.
- If pursued:
  - Trade it on **US and Japanese equity indices** through the **cheapest vehicle available** (index futures or cash ETFs rather than CFDs).
  - Use the **fixed settings above.**
  - Start with a demo or small-size forward test.
  - Apply the pause rules in Section 8.
- Do **not** add the instruments that failed here (FTSE, Hang Seng, China A50, EEM), and do not add non-equity assets.

## 10. Signal frequency: can breadth deliver ≥ 1 winning signal per day? (`frequency_mr.py`)

The same fixed RSI(2) rules were applied to wider instrument sets. Results cover 2019–2026, net of costs, at 0.5% risk per trade.

| Universe | Signals/day | Winning signals/day | Days with ≥ 1 win | Days with no signal | Win % | Exp. | PF | Max DD |
|---|---|---|---|---|---|---|---|---|
| 4 core indices | 0.13 | 0.10 | 7% | 92% | 77.2 | +0.093R | 1.60 | 2.3% |
| 24 equity indices + ETFs | 0.62 | 0.45 | 20% | 75% | 72.7 | +0.064R | 1.38 | 11.0% |
| + 88 single stocks | 2.67 | **1.82** | 57% | 33% | 68.1 | +0.034R | 1.18 | 34.9% |
| + stocks, max 20 open | 2.00 | **1.35** | 55% | 34% | 67.6 | +0.028R | 1.15 | 21.9% |
| + stocks, max 10 open | 1.31 | 0.87 | 48% | 39% | 66.7 | +0.025R | 1.14 | 12.5% |

**Single stocks** (4,830 trades, split-adjusted; 19 splits detected and corrected):
- Win rate 66.6%, expectancy +0.022R, PF 1.12. Gross (before costs) PF 1.32.
- 58% of stocks were positive.
- **The timing does not beat random entries on the same stocks** (p = 0.29).
- The stock list is today's list, so survivorship bias flatters long-only results.

**Conclusion:** an *average* of at least 1 winning signal per day is only reachable by adding single stocks. That dilutes the edge to PF ≈ 1.15, the stock part has no proven timing edge, and drawdown grows. **No version produces a win every day.** Signals cluster on sell-off days: up to 32 in one day, and none at all on a third of days. The best balance of quality and frequency is the 24 indices + ETFs: about 3 signals a week, PF 1.38.

## 11. The 1-hour version fails (`hourly_mr.py`)

The same rules were tested on 1-hour bars, pre-registered, with nothing tuned. Same 24 indices and ETFs, entries only during each market's cash session, 3×ATR(14) stop, 24-bar time stop, same costs.

| Variant | Trades/yr | Win % | Exp. | PF | Instruments positive |
|---|---|---|---|---|---|
| H-A: hourly trend (close > SMA200 H1) + RSI(2,H1) < 10 | 837 | 60.9 | −0.043R | 0.83 | 1 / 24 |
| H-B: daily trend + RSI(2,H1) < 10 | 1,239 | 60.4 | −0.043R | 0.84 | 1 / 24 |

- Neither variant beat random entry bars (shared-timestamp control, p ≈ 0.7).
- More signals, but the edge is gone: intraday dips of this size are mostly noise plus costs.
- **Decision: daily bars only.**

## 12. Live engine (`dipbuy/`) and what changes versus the backtest

The MT5 engine (`dipbuy/engine.py`, rules in `dipbuy/strategy.py`) was checked by replaying real daily history through it with a fake MT5 (`dipbuy/tests/test_replay.py`). The engine runs at each day's open, then the day plays out with server-side stops.

| Market | Backtest trades | Engine trades | Same entry day | Stop-outs (bt / engine) |
|---|---|---|---|---|
| US500 | 121 | 121 (+1 still open) | 100% | 14 / 14 |
| JP225 | 113 | 113 (+1 still open) | 100% | 15 / 15 |
| IWM | 62 | 62 | 100% | 7 / 7 |
| XLF | 65 | 65 | 100% | 8 / 8 |

Indicators match the research code exactly (RSI and ATR difference 0; SMA ≈ 1e-11). `dipbuy/tests/test_smoke.py` also covers dry-run mode, live-account refusal, the drawdown pause and the daily cap.

**One deliberate difference: exits.** The backtest exits at the close on which RSI(2) rises above 70 (or day 10). The engine only knows that close once it has printed, so it exits at the next open. Effect on the pre-registered PRIMARY set, net of costs:

| Exit | Trades | Win % | Exp. | PF |
|---|---|---|---|---|
| At the signal close (backtest) | 1,307 | 71.0 | +0.043R | 1.23 |
| At the next open (engine) | 1,307 | 68.4 | +0.036R | 1.19 |

**Expected frequency on the engine's instrument list** (5 index CFDs + 12 ETFs, net of costs):
- 2019–2026: about 112 trades/yr (≈ 2 a week), signals on 22% of trading days, win 74%, PF 1.55.
- 2023–2026: about 135 trades/yr, signals on 26% of trading days, win 76%, PF 1.99.
- These two rows are **optimistic**. The list leaves out markets that failed validation (selection bias), and 2023–2026 was a strong bull market. Plan on the PRIMARY figures above.
- A broker with only index CFDs gives about one signal a week.

## 13. VWAP intraday setups fail (`vwap_test.py`, rules in `VWAP_PREREG.md`)

Three popular VWAP day-trading setups were tested exactly as pre-registered, with nothing tuned:
- **A: trend pullback.** VWAP rising, price above it, a pullback that holds, then a confirming candle.
- **B: reclaim / rejection.** Price crosses VWAP, retests it and holds.
- **C: band fade.** On flat-VWAP days, fade a touch of the 2σ band back to VWAP.

Test design:
- Dukascopy M1 data with session-anchored VWAP on US500, NAS100, US30, DE40, UK100, JP225 (2011/13–2026) and XAUUSD (2006–2026).
- 5-minute signals, minute-level stops, flat by the session close.
- Costs ≈ IC Markets raw spread + commission + 1 tick.

| Setup | Trades/yr | Win % | Before costs | **After costs** | PF | Holdout 2023–26 | vs random entries | Markets positive |
|---|---|---|---|---|---|---|---|---|
| A pullback | 1,293 | 34.4 | +0.015R | **−0.153R** | 0.80 | −0.159R, PF 0.79 | p = 0.16 | 0 / 7 |
| B reclaim | 1,734 | 34.5 | +0.011R | **−0.154R** | 0.80 | −0.140R, PF 0.81 | p = 0.15 | 0 / 7 |
| C band fade | 1,259 | 54.1 | −0.016R | **−0.105R** | 0.76 | −0.103R, PF 0.76 | p = 0.85 | 0 / 7 |

- **Every pass criterion fails for every setup.** All 27 neighbouring parameter variants are negative too (−0.09R to −0.17R), in development and holdout alike.
- **Why:** before costs each setup is about breakeven, so VWAP timing carries no measurable edge. Its timing is no better than random entries with the same stops. The tight VWAP stops (about 1 five-minute ATR) make costs about 0.17R per trade for A and B, and 0.09R for C.
- **Scale of the loss:** at 0.5% risk per trade, setup A's ≈1,300 trades a year at −0.153R would lose about 63% of the account in a year, even with position size shrinking as equity falls.
- The band fade wins more often than it loses (54%) and still loses money: the same high-win-rate, negative-expectancy pattern as the rejected configuration in Section 4.
- "Order flow" was represented by a confirming candle; this data has no bid/ask volume. A footprint-based version is untested, but it would face the same cost hurdle.
- An independent minute-by-minute re-simulation of 840 sampled trades matched the test code exactly.

**Decision: not traded.** The daily dip-buy (Sections 9–12) remains the only strategy that passed.

## 14. Reproduce

```
cd research
python resample.py <dukascopy_m1.csv> data/<symbol>      # then rename to data/<symbol>/h1.csv
python edge_diagnostic.py          # baseline + entry-edge test
python walkforward.py              # 40-variant grid, rolling WF, holdout (≈3 min, 2 cores)
python robustness.py A             # metrics, Monte Carlo, regimes, stress for track A
python meanrev.py                  # alternative: WF + holdout
python mr_robust.py idx_metals_long B
python resample_daily.py data_daily <m1 csvs...>   # daily bars for validation
python validate_mr.py              # pre-registered out-of-instrument validation (Section 9)
python frequency_mr.py             # signal frequency by universe breadth (Section 10)
python hourly_mr.py                # 1-hour version (Section 11)
python ../dipbuy/tests/test_replay.py   # engine vs backtest parity (Section 12)
python ../dipbuy/tests/test_smoke.py    # dry run, live refusal, pause, daily cap
python vwap_extract.py <m1.csv> data/vwap/<MKT>.npz <utc_from> <utc_to>   # per market, see file header
python vwap_test.py                # VWAP setups (Section 13), ~4 min
```
Every variant and trade is saved under `research/out_*`.
