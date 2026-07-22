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

## Remaining work for issue #18

This PR completes the execution adapter only; it does **not** complete issue #18. The adapter is
not integrated into the controlled **All strategy comparison** workflow, its output is not published
as a workflow artifact, and it is not part of the native-Freqtrade comparison schema. Those follow-up
changes must preserve the explicit distinction between `one_shot_capital` and
`recurring_monthly_contributions` and must compare contribution-neutral performance rather than a
naive return against final contributed capital. Issue #18 remains open.
