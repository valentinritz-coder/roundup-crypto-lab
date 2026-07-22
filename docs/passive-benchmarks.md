# Passive benchmarks

This lab compares **Buy & Hold (immediate)**, **Daily DCA**, and **Weekly DCA** for each independently evaluated pair. They are deterministic passive references, not Freqtrade strategies and not a multi-asset portfolio.

## One investor plan, separate deployment

Every result is funded by the same immutable investment plan: `initial_capital`, `monthly_budget`, `fee_ratio`, and `contribution_day`. The initial capital is credited at the start of the strict `YYYYMMDD-YYYYMMDD` timerange. Monthly cash flows are credited at `00:00 UTC` on `contribution_day`; when a month lacks that day, the event uses its last calendar day. The range is **start-inclusive and end-exclusive**, so only events in `[start, end)` occur.

Contributions are investor cash flows, not purchases. At `contributed_at`, the engine increases both `cash_balance` and `cumulative_contributions`, even when no candle or purchase exists at that instant. A planned purchase executes at the open of the first available candle at or after `scheduled_at`; at `executed_at`, it decreases cash by its gross amount and increases crypto quantity by its post-fee amount divided by that open price. An absent scheduled candle therefore shifts execution forward without rewriting `contributed_at`. No execution occurs after the timerange and capital without an eligible DCA slot remains cash.

Daily and weekly methods divide each received cash flow over their eligible slots before the next cash flow (or range end). Immediate deploys each contribution when it arrives. There is intentionally no `monthly_dca` duplicate: monthly investor cash flows and immediate deployment are the single defined monthly-cash behavior. All methods receive the same contribution calendar and `total_contributions`, but can have different invested capital and cash balances.

## Accounting, fees, and performance

For every candle, processing order is deterministic: **(1)** credit all uncredited contributions with `contributed_at <= candle timestamp` in schedule order; **(2)** execute purchases for that candle in scheduled-time order; **(3)** mark crypto at that candle close. The equity curve exposes `cash_balance`, `crypto_value`, `portfolio_value`, `net_value`, `cumulative_contributions`, `capital_invested`, and `cumulative_fees_paid`.

`portfolio_value = cash_balance + crypto_value` and `net_value = portfolio_value - cumulative_contributions`. `total_contributions` is always derived from the investment-plan schedule, while `capital_invested` is the sum of executed gross purchases; consequently `total_contributions = capital_invested + cash_balance`. All internal monetary calculations use `Decimal`. Fees are deducted only from executed gross purchases: `net_contribution = gross_contribution × (1 - fee)`.

Raw drawdown uses total portfolio value including cash. For the contribution-neutral curve, shares are issued for every contribution immediately before buys at the portfolio's candle-open value; purchases do not issue shares. Each candle-close portfolio value divided by those shares yields `time_weighted_share_value`. Thus an investor deposit is not performance, an uninvested cash balance has no market return, and an executed fee is visible immediately.

## Local use and migration

```bash
python -m roundup_crypto_lab.passive_benchmarks \
  --timerange 20260123-20260722 \
  --initial-capital 200 --monthly-budget 40 --contribution-day 23 \
  --output-json artifacts/benchmarks/passive-benchmarks.json \
  --output-dir artifacts/benchmarks
```

`--daily-contribution` and `--weekly-contribution` (including their `=VALUE` forms) are removed and fail with a migration message; use `--monthly-budget`. The former public `buy_and_hold()` and `dca()` helpers also fail with a migration error rather than recreating unfair independent budgets.

The engine reads prepared Kraken Feather data only, using the `4h` timeframe. It remains dry-run, spot-only, long-only research and models no slippage, taxes, deposit fees, withdrawal fees, liquidity limits, or sales.
