# Improved gold strategy: results and stress tests (plan in IMPROVE_PREREG.md)

## Selection (pre-registered, chosen on 2006–16 only)
Net R per trade, after costs.

| Variant | 2006–16 | 2017–22 | **2023–26 holdout** | Full |
|---|---|---|---|---|
| V0 repo (bugs fixed) | −0.137 | −0.015 | +0.006 | −0.075 |
| V1 repo + trailing exit (H1) | +0.138 | +0.008 | +0.100 | +0.091 |
| **V2 H4 EMA cross + daily trend + trailing stop (selected)** | **+0.185** | **+0.011** | **+0.148 (PF 1.35)** | **+0.123 (PF 1.26)** |
| V3 daily 50/20 breakout | +0.101 | +0.450 | +0.664 | +0.294 |
| V4 daily breakout, long only (< 50 trades in 2006–16: not eligible) | +0.415 | +1.005 | +1.029 | +0.710 |

- **V2 passes every pre-registered check:**
  - positive on validation and holdout;
  - holdout PF 1.35;
  - beats random entries, p < 0.003 (random +0.005R);
  - positive at 2× costs (+0.048R).
- **But validation (2017–22) was only breakeven** (+0.011R).
- V3/V4 look stronger, but they weren't picked by the rule. Choosing them now would be hindsight, so they'd need their own forward test.

## Stress tests of V2 (rules unchanged)
**Costs and execution:**
- 2× costs: +0.048R. **3× costs: −0.027R** (break-even at ≈2.6×).
- Entry one H4 bar late: +0.139R.
- **Stops slipping 0.25 ATR: +0.030R.** Stop slippage is the main fragility.
- All three together: −0.108R.

**Monte Carlo** (10,000 reshuffles, 1% risk per trade):
- Max drawdown: median 21%, 95th percentile 33%.
- A typical year makes +2.5%, and 38% of years lose.

**Regimes:**
- Wins in trends either way: 2006–11 bull +0.19R from longs; 2011–15 bear +0.20R from shorts; 2023–26 +0.15R.
- Loses in the 2016–18 range (−0.06R); weak in the 2019–22 chop (+0.06R).
- Apr 2013 crash +20R; 2022 +6.7R; 2008 −0.9R.

**Walk-forward re-tuning** (each year, pick from 9 nearby settings using only the previous 4 years), 2011–26:
- **+0.171R** walk-forward vs **+0.099R** fixed. Best in hindsight would have been +0.216R.
- It chose the tighter 1.5×ATR initial stop in 14 of 16 years. That is the one further improvement that survives an honest test.

**Other markets, same rules unchanged:**
- Positive on only **12 of 34**.
- Silver −0.004R; FX 2/7 positive (mean −0.02R); indices 2/8 (mean −0.07R); US ETFs mostly negative.
- Bitcoin +0.42R and Ethereum +0.39R (both beat random, p < 0.02).
- **It's not a universal trend effect.** It works on gold and crypto, not on silver, FX or indices, so there's a real risk that gold's result is partly specific to its history.

## Bottom line
- **It improved:** the repo at −0.075R → pre-registered V2 at +0.123R → walk-forward re-tuned at +0.171R per trade. That is about 3–4.5% a year at 1% risk, with ~20% typical drawdowns.
- **It won't keep getting better by tuning.** What's left is structural: losing range years, sensitivity to cost and stop slippage, and no transfer to silver, FX or indices. More tweaks would only fit the past.
- **Next honest step if wanted:** forward-test V2 (with the walk-forward stop rule) on a demo, separate from the dip-buy engine.
