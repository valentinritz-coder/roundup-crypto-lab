# Active backtests and investor cash flows

Freqtrade 2026.6 accepts a starting balance for a historical backtest but has no public interface
for depositing cash into its simulated wallet at historical timestamps. The lab must not work around
that limitation by modifying Freqtrade internals or by giving a backtest all future contributions at
its start.

`roundup_crypto_lab.freqtrade_active` connects the adapter to the actual strategy source. It imports
each selected class from `user_data/strategies` and calls its `populate_indicators`,
`populate_entry_trend`, and `populate_exit_trend` methods; no indicator or signal formula is copied
into the accounting code. A signal calculated from completed candle N is shifted to execute at
candle N+1's open. The wallet decision callback receives no OHLCV values, so current-candle close,
high, low, and volume cannot influence an order at that candle's open.

The adapter uses the shared `InvestmentPlan` and `contribution_schedule` exactly as passive
benchmarks do. Contribution events are processed before the N+1 execution snapshot, recorded
independently, and never generate a strategy signal.

Before evaluating `[start, end)`, the bridge retains the selected strategy's declared
`startup_candle_count` candles and runs all three strategy population methods over warm-up plus
evaluation data. It fails if that history is unavailable. Only post-start execution candles and
cash flows are emitted. The bridge and CLI are deliberately single-pair: select `BTC/EUR` or `ETH/EUR`;
the pair is passed as strategy metadata and deterministically maps to its matching prepared Feather
file. This is not a two-pair portfolio simulation; the controlled All strategy comparison workflow
remains native-Freqtrade only.

## Modes and timestamps

- `one_shot_capital` credits only `initial_capital` at the timerange start, preserving the existing
  one-shot experiment mode.
- `recurring_monthly_contributions` credits `initial_capital` and the scheduled monthly budget.

The timerange is `[start, end)`. A contribution is credited at the first supplied candle at or
after its UTC timestamp; the ledger records both the investor timestamp and the actual credit
candle. Contributions at or after `end` are excluded. Buy stakes are rejected when they exceed the
cash snapshot available at their candle, and a contribution while a trade is open is cash only—it
does not alter that trade's original quantity or stake.

## Output and limitation

The result contains a complete `contribution_ledger`, trade ledger, and equity curve. Each equity
row reports free cash, current deployed capital, cumulative gross deployed capital, crypto value,
total equity, cumulative contributions, and
`investment_return = equity - cumulative_contributions`; the time-weighted share value is included
for contribution-neutral performance and drawdown analysis. All money is represented by `Decimal`
inside the adapter.

The adapter is currently available through its Python API and CLI only. It is not yet part of the
controlled All strategy comparison workflow, because its results are not yet lifecycle-equivalent
to native Freqtrade results or included in the comparison schema.

This adapter is intentionally not a multi-asset allocator and does not replace native Freqtrade
reporting. It supports only signal-driven, next-open entries and full exits, one-position limit,
configured fee, and `tradable_balance_ratio` stake sizing. It does **not** model fixed or custom
stop-loss execution, ATR stop updates, intrabar ordering, limit-order fills, ROI exits, order
timeouts, the native Freqtrade lifecycle, multi-asset allocation, or combined active/passive
reporting. Native CLI backtests remain the authoritative one-shot reference. Full issue #18
therefore remains open for lifecycle and reporting follow-up work.
