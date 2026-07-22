# Active backtests and investor cash flows

Freqtrade 2026.6 accepts a starting balance for a historical backtest but has no public interface
for depositing cash into its simulated wallet at historical timestamps. The lab must not work around
that limitation by modifying Freqtrade internals or by giving a backtest all future contributions at
its start.

`roundup_crypto_lab.active_backtests` is therefore a repository-owned, deterministic adapter for
an active, one-asset, one-position execution stream. It uses the shared `InvestmentPlan` and its
`contribution_schedule` exactly as passive benchmarks do. A strategy decision provider receives a
wallet snapshot at each candle and can issue its normal buy, sell, or hold decision. Contribution
events are processed before that snapshot, are recorded independently, and never invoke the
decision provider: a contribution cannot itself create a trade.

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
row reports free cash, deployed capital, crypto value, total equity, cumulative contributions, and
`investment_return = equity - cumulative_contributions`; the time-weighted share value is included
for contribution-neutral performance and drawdown analysis. All money is represented by `Decimal`
inside the adapter.

This adapter is intentionally not a multi-asset allocator and does not replace Freqtrade's native
backtest reporting. To connect a Freqtrade strategy faithfully, feed the adapter the strategy's
causal per-candle decisions and the same execution prices/fee rules used by the selected research
configuration. That makes the cash-flow accounting auditable without an unsupported monkey patch,
but it means a direct Freqtrade CLI backtest remains a one-shot-capital backtest until Freqtrade
exposes historical wallet deposits through a public interface.
