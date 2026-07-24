# Cash-flow-aware performance and risk metrics

The repository reports investor cash flows separately from method performance. Every official active and passive artifact exposes a `cash_flow_metrics` object with schema version `cash-flow-metrics/v1`.

## Timing convention

Investor contributions use their actual UTC timestamps and are credited before any execution on the same candle. The time-weighted share mechanism issues new shares at the portfolio value immediately before the contribution, so adding cash neither creates a gain nor erases a loss.

## Investor outcome metrics

- `total_contributions`: all external investor cash credited during the scenario.
- `final_value`: final marked-to-market cash plus asset value. This preserves the historical simulation convention and does not deduct a hypothetical final sale fee.
- `final_cash`: uninvested cash at the final snapshot.
- `final_asset_value`: marked-to-market crypto value at the final snapshot.
- `terminal_liquidation_value`: final cash plus final asset value net of one configured exit fee.
- `profit_abs`: final marked-to-market value minus total contributions.
- `simple_return_on_contributions`: descriptive ratio only; it is not used as a strategy-skill metric.
- `money_weighted_return`: annualized XIRR using negative investor contributions and positive terminal liquidation value.
- `money_weighted_return_status`: `converged`, `undefined_no_negative_cash_flow`, `undefined_no_positive_cash_flow`, or `not_converged`. Undefined values serialize as JSON `null`, never NaN or Infinity.

## Method-skill and risk metrics

- `time_weighted_return`: final contribution-neutral share value minus one. This is the primary method-performance metric for recurring-contribution scenarios.
- `max_drawdown_time_weighted`: drawdown of the contribution-neutral share-value curve.
- `max_drawdown_raw_portfolio`: drawdown of the raw marked-to-market portfolio value. Contributions can affect this operational measure, so it is not the primary strategy comparison metric.
- `average_capital_deployed`: time-weighted average marked-to-market asset value.
- `capital_utilization_ratio`: time-weighted average of asset value divided by total portfolio value. Repeatedly recycling the same cash does not inflate this measure.

The final curve point is weighted until the end-exclusive scenario timestamp. Active and passive methods therefore use the same duration convention.

## Degenerate scenarios

A no-trade, all-cash portfolio with contributions only has:

- TWR 0%;
- XIRR 0%;
- both drawdowns 0%;
- average deployed capital 0;
- utilization 0%.

A scenario without both a negative and a positive dated cash flow reports `money_weighted_return: null` with an explicit status.

## Compatibility

Historical active fields `contribution_neutral_return` and `contribution_neutral_max_drawdown` remain available. Validators require them to equal `time_weighted_return` and `max_drawdown_time_weighted` respectively. Historical passive summary fields also remain available, while the shared nested block is the canonical schema for future unified comparison work.
