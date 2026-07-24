# Research strategy batch one

This batch adds four causal, spot, long-only 4h hypotheses without adding another data source,
informative timeframe, leverage, shorting, or custom stop callback. The fixed `-12%` emergency stop
and vectorized exits deliberately keep the first implementation relatively insensitive to the known
`stop_loss` versus `exit_signal` precedence limitation.

The parameters below are research defaults chosen before viewing batch results. They must not be
retuned from one favorable timerange and then described as evidence.

| Strategy | Family | Entry hypothesis | Exit hypothesis |
| --- | --- | --- | --- |
| `RoundupScientificControlBreakoutStrategy` | Scientific control | Close above the prior 20-bar high, with no trend, ATR, or volume-strength filter. | Close below the prior 10-bar low. |
| `RoundupRiskAdjustedMomentumStrategy` | Volatility-normalized momentum | Positive 12-bar momentum greater than one matching-horizon realized-volatility unit, above SMA100, while the current range remains below `2.5 × ATR14`. | Normalized momentum turns negative or close falls below SMA20. |
| `RoundupBullPullbackRsiStrategy` | Conditional mean reversion | SMA100 is rising, price remains above it, the previous bar was RSI2-oversold, and the current bullish bar starts a recovery while still below SMA20. | RSI2 exceeds 55 or price returns to SMA20. |
| `RoundupDistanceReversionStrategy` | Conditional mean reversion | SMA100 is rising, price remains above it, close is more than `1.5 × ATR14` below EMA20, and the bar closes in its upper 40%. | The ATR-normalized distance reaches zero or price returns to EMA20. |

## Causality

Rolling channel levels use `.shift(1)`. Recovery rules use only current and earlier completed rows.
Freqtrade evaluates the vectorized signal on completed candle N and the repository adapter schedules
execution for candle N+1. No rule reads a future candle, uses negative shifts, or indexes the final
row directly.

## Controlled comparison

`All strategy comparison` now runs eleven strategies in one fixed order and includes native
Freqtrade exports, contribution-aware adapter results, and the one-shot differential when selected.
The new strategies use fixed stops, so the adapter does not need a new custom-stop mapping.

The first useful experiments are not a winner-takes-all leaderboard. Compare:

- the scientific control against the existing filtered breakouts;
- risk-adjusted momentum against raw breakout/trend variants;
- the two mean-reversion strategies against each other;
- native signal metrics before interpreting contribution-aware portfolio results;
- BTC/EUR and ETH/EUR across separate market regimes and fee assumptions.

Reject a strategy when its apparent advantage depends on one asset, one narrow timerange, or a small
parameter perturbation. A green workflow proves reproducibility and technical validity, not future
profitability.
