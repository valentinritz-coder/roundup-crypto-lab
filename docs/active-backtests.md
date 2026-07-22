# Active cash-flow backtests

The repository-owned adapter adds recurring investor deposits to an auditable, single-pair spot execution model. It invokes the selected Freqtrade strategy for indicators and signals, then resolves the effective repository `config.json` and strategy lifecycle settings. It rejects non-spot, non-long-only, multi-position, trailing-stop, active-ROI, and unsupported custom-stop configurations rather than silently approximating them.

## Causal execution convention

1. A contribution is credited to wallet cash immediately before the first eligible candle's execution snapshot. It never changes an existing position quantity or historical stake.
2. Signals from completed candle **N** execute at candle **N+1** open.
3. The fixed strategy stop starts at entry price times `1 + stoploss`. The supported custom stop is the repository's `open - 2 × ATR14`; ATR is taken only from the prior completed candle and a stop can only tighten.
4. Before a signal exit, a stop is tested against the candle: an open at/below stop fills at open (gap-through); otherwise a low at/below stop fills at the stop. Thus stop-loss has deterministic priority over an overlapping exit signal.
5. Entry and exit fees use the investment plan fee ratio. Closing a trade resets current deployed capital; cumulative gross deployed stays historical.
6. After execution, the candle close marks open crypto. An end-open trade is retained with `exit_reason: null` and `end_of_range_position: open_marked_at_final_close`; it is never force-closed.

This is deliberately a narrow OHLC convention, not a simulation of order books, limit-order timeouts, or multi-asset allocation. It matches the relevant strategies' spot, long-only, one-position, fixed/ATR stop and exit-signal scope while keeping external contributions separate from strategy return.

## Native differential scope

The differential harness generates (rather than edits) a temporary native configuration from
`user_data/config.json`. It replaces the whitelist with exactly one selected pair (`BTC/EUR` or
`ETH/EUR`) and records a canonical SHA-256 configuration digest. Before an adapter invocation, the
data filename, strategy timeframe metadata, and generated native whitelist must agree. This is an
offline fixture contract: it does not download market data.

The comparison is deliberately limited to the lifecycle fields implemented by the adapter: entry
and exit times/prices, gross stake, quantity (with an explicit `1e-8` decimal rounding tolerance),
entry/exit fees, exit reason, and final free cash, crypto mark, and equity. Any other Freqtrade
behavior—including order-book, limit-order, and unsupported lifecycle behavior—is outside this
claim. Passing this differential test therefore does **not** establish general Freqtrade
equivalence.

The executable reference is `python -m pytest -vv tests/test_freqtrade_differential.py`. It writes
150 deterministic BTC/EUR 4-hour candles to a temporary Freqtrade Feather directory, runs
`python -m freqtrade backtesting --config <temporary-config> --datadir <temporary-datadir>
--strategy RoundupBreakoutStrategy --timeframe 4h --timerange 20260121-20260126 --fee 0.005
--export trades`, then runs the adapter with `one_shot_capital`. The fixture has 120 warm-up
candles, a breakout followed by an `exit_signal`, and a second breakout whose entry candle reaches
the fixed -12% stop. The test disables the strategy's ATR custom-stop through the generated
configuration only; the strategy source is unchanged.

Native `open_date`, `close_date`, `open_rate`, `close_rate`, `stake_amount`, `amount`,
`fee_open`, `fee_close`, and `exit_reason` normalize respectively to adapter entry/exit timestamps,
prices, gross stake, quantity, fees, and reason. Native stake excludes entry fee, as does the
adapter stake; both wallets debit stake plus the entry fee. `trailing_stop_loss` is normalized to
`stop_loss`. Timestamps, prices, gross stake, and exit reasons compare exactly. The only tolerance
is `1e-8` for native exported quantity and its directly derived entry/exit fees. The native reasons
accepted by this scope are only `exit_signal`, the repository strategy's `close_below_sma20` exit
tag, `stop_loss`, and `trailing_stop_loss`; `close_below_sma20` normalizes to `exit_signal` and the
latter normalizes to `stop_loss`, while every other native reason fails validation.

The differential proof is only for `one_shot_capital`. Native Freqtrade has no equivalent for the
adapter's investor contribution ledger; recurring mode remains separately tested with the real
strategy and is not claimed as native-equivalent.

## Controlled recurring comparison

The All strategy comparison workflow produces a versioned `active-strategy-result/v1` JSON file for each strategy and a combined controlled-comparison artifact. Its **Native Freqtrade one-shot reference** and **Active investor cash-flow simulation** sections are separate result families: native Freqtrade `profit_total` is never used to rank recurring simulations.

`investment_return` is final equity minus all contributed capital. It is useful for reconciling the wallet, but dividing it by final contributions is misleading because contributions arrive at different dates and have different market exposure. Instead, use the contribution-neutral return and maximum drawdown, which track the value of time-weighted investor shares after each contribution. These are comparable only among runs with identical pair, timerange, fee, and investment-plan settings.
