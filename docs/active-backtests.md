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

The **All strategy comparison** workflow accepts `capital_mode`, `initial_capital`,
`monthly_budget`, and `contribution_day`; it emits one JSON artifact per active strategy under
`artifacts/results/active/`. Each contains the plan, schedule, contribution ledger, trade ledger,
and equity curve. Both funding modes use the same plan definition as passive methods.

This adapter is intentionally not a multi-asset allocator and does not replace native Freqtrade
reporting. It reproduces the strategy's indicator/signal methods, next-open signal execution,
one-position limit, configured fee, and `tradable_balance_ratio` stake sizing. It does **not** yet
reproduce native limit-order fill modelling, intrabar/custom stop-loss behaviour, ROI exits, or
native order timeouts; recurring artifacts must not be represented as byte-for-byte native
Freqtrade-equivalent results. Native CLI backtests remain the one-shot reference until Freqtrade
exposes historical wallet deposits through a public interface.
