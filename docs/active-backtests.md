# Active cash-flow backtests

The repository-owned adapter adds recurring investor deposits to an auditable, single-pair spot execution model. It invokes the selected Freqtrade strategy for indicators and signals, then resolves the effective repository `config.json` and strategy lifecycle settings. It rejects non-spot, non-long-only, multi-position, trailing-stop, active-ROI, and unsupported custom-stop configurations rather than silently approximating them.

## Causal execution convention

1. A contribution is credited to wallet cash immediately before the first eligible candle's execution snapshot. It never changes an existing position quantity or historical stake.
2. Signals from completed candle **N** execute at candle **N+1** open.
3. The fixed strategy stop starts at entry price times `1 + stoploss`. The supported custom stop is the repository's `open - 2 × ATR14`; ATR is taken only from the prior completed candle and a stop can only tighten.
4. Before a signal exit, a stop is tested against the candle: an open at/below stop fills at open (gap-through); otherwise a low at/below stop fills at the stop. Thus stop-loss has deterministic priority over an overlapping exit signal.
5. Entry and exit fees use the investment plan fee ratio. Closing a trade resets current deployed capital; cumulative gross deployed stays historical.
6. After execution, `mark_price` records the candle close used to value open crypto. An end-open trade is retained with `exit_reason: null` and `open_position_state: open_marked_at_final_close`; it is never silently force-closed.

This is deliberately a narrow OHLC convention, not a simulation of order books, limit-order timeouts, or multi-asset allocation.

## Native differential scope

The differential harness generates a temporary single-pair native configuration from `user_data/config.json`, records a canonical SHA-256 configuration digest, and compares only the lifecycle fields owned by the adapter.

The one-shot proof compares entry and exit timestamps and prices, gross stake, quantity, entry and exit fees, normalized exit reason, final free cash, final crypto value, and final equity. Native exported quantity and its directly derived fees use the documented `1e-8` tolerance. Adapter stake is normalized to Freqtrade's eight-decimal export representation; prices, timestamps, reasons, and the normalized stake remain exact comparisons.

Accepted native reasons are `exit_signal`, `close_below_sma20`, `stop_loss`, and `trailing_stop_loss`. The repository exit tag normalizes to `exit_signal`; `trailing_stop_loss` normalizes to `stop_loss`. Every other reason fails validation.

The workflow's differential scope requires both native and active positions to be closed at timerange end. A force-exit or an open marked position is rejected explicitly rather than being represented as proven equivalent. Passing the differential therefore does **not** establish general Freqtrade equivalence.

Each strategy produces a `one-shot-differential/v1` result. The aggregator requires exactly the seven strategies in repository order, one common experiment ID, pair, timeframe, timerange and capital mode, a `passed` status, and the expected checked-field set. The combined artifact is validated again before it is embedded in `controlled-comparison/v1` and displayed in the GitHub job summary.

## Controlled active comparison

The All strategy comparison workflow produces:

- seven preserved native Freqtrade ZIP files;
- seven `active-strategy-result/v1` JSON files;
- `all-strategies-comparison.json`;
- `metadata.json`, including the generated configuration path and digest;
- `controlled-comparison.json`;
- `job-summary.md`;
- in one-shot mode only, seven individual differential results and one combined `one-shot-differential.json`.

Before reporting or upload, validation reconciles experiment identity, pair, timeframe, timerange, capital mode, investment plan, fee, generated config and digest. It also validates every contribution, trade and equity row, including chronological boundaries, positive and finite amounts, wallet cash reconciliation, fee totals, non-overlapping single-position lifecycle, `equity = free_cash + crypto_value`, `investment_return = equity - cumulative_contributions`, mark-to-market crypto value, and final metrics.

The **Native Freqtrade one-shot reference** and **Active investor cash-flow simulation** sections are separate result families. Recurring simulations are never ranked using native `profit_total`.

`investment_return` is final equity minus all contributed capital. Dividing it by final contributions is misleading because contributions arrive on different dates. Contribution-neutral return and maximum drawdown instead use the time-weighted investor-share series. They are comparable only among runs with identical experiment parameters.

## Manual workflow verification

Run **All strategy comparison** with a supported pair and a timerange containing sufficient warm-up data. First use `recurring_monthly_contributions` and verify the seven native ZIPs, seven active JSON files, metadata, controlled comparison and summary. Then use `one_shot_capital` on a timerange that closes every position and verify the seven individual differential files, their combined artifact, seven `passed` statuses, and the one-shot differential table in the summary.
