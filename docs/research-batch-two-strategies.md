# Research strategy batch two

This batch adds four OHLCV-only, causal, long-only strategies to the controlled comparison. The defaults below are preregistered before any batch-two performance result is observed.

All four strategies use the repository's existing operational contract:

- 4h candles;
- spot long-only execution;
- one open trade at a time;
- completed-candle signals executed on the next candle open by the contribution adapter;
- disabled ROI target;
- fixed `-12%` stop inherited consistently by native and adapter runs;
- vectorized entry and exit signals;
- no trailing stop, custom stoploss, custom exit, or position adjustment.

## RoundupObvConfirmedBreakoutStrategy

**Hypothesis:** a price breakout supported by positive volume flow and above-normal participation should be more durable than a price-only breakout.

Entry requires:

- close above the prior 20-bar high;
- OBV above its 20-bar moving average;
- volume above 1.20 times its 20-bar moving average;
- close above SMA100.

Exit occurs when OBV falls below its 20-bar average or price closes below SMA20.

## RoundupTrendQualityKerAdxStrategy

**Hypothesis:** smooth and directional trends should be more tradable than noisy trends, even when both satisfy a normal trend filter.

Entry requires:

- Kaufman Efficiency Ratio over 20 bars above `0.35`;
- ADX14 above `25`;
- close above SMA100;
- close above the prior 10-bar high.

Exit occurs when KER20 falls below `0.20` or price closes below SMA20.

## RoundupDonchianRetestStrategy

**Hypothesis:** a 55-bar breakout that survives an orderly retest should be higher quality than an immediate breakout chase.

The strategy:

1. records a close above the prior 55-bar high;
2. carries the breakout level for at most six later candles;
3. accepts a retest only when price revisits the level within an ATR14 buffer and does not close materially below it;
4. enters only on a later bullish recovery above that recorded level while price remains above SMA100.

Exit occurs when price closes below the prior 20-bar low or below SMA20.

## RoundupCapitulationRecoveryStrategy

**Hypothesis:** a high-volume downside shock may mean-revert only after the following candle demonstrates a real recovery, rather than merely because the first candle appears oversold.

The prior candle must show:

- a close-to-close move of at most `-1.50 ATR14`;
- relative volume of at least `1.50` versus the 20-bar average;
- a close in the bottom quarter of its range.

The current candle must:

- close above the midpoint of the prior candle;
- close above its open;
- show RSI3 rising from the prior candle.

Exit occurs when price reaches EMA20 or RSI3 rises above `55`.

## Evaluation rules

Batch two must be evaluated on the same BTC/EUR and ETH/EUR pairs, timeranges, fee, capital modes, and passive benchmarks as the existing roster. Native Freqtrade remains the signal-quality reference. The contribution-aware adapter remains the investor cash-flow reference.

A strategy is not retained from one attractive return alone. Review must include trade count, profit factor, expectancy, drawdown, fee sensitivity, cross-asset consistency, and differential diagnostics. Donchian retest is expected to have medium path sensitivity; small native-versus-adapter differences must therefore be interpreted conservatively.
