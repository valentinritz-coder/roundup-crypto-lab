# Passive benchmarks

This lab compares **Buy & Hold (immediate)**, **Daily DCA**, and **Weekly DCA** for each independently evaluated pair. They are deterministic passive references, not Freqtrade strategies and not a multi-asset portfolio.

## One investor plan, separate deployment

Every result is funded by the same immutable investment plan: `initial_capital`, `monthly_budget`, `fee_ratio`, and `contribution_day`. The initial capital is credited at the start of the strict `YYYYMMDD-YYYYMMDD` timerange. Monthly cash flows are credited at `00:00 UTC` on `contribution_day`; when a month lacks that day, the event uses its last calendar day. The range is **start-inclusive and end-exclusive**, so only events in `[start, end)` occur.

Contributions are investor cash flows, not purchases. At `contributed_at`, the engine increases both `cash_balance` and `cumulative_contributions`, even when no candle or purchase exists at that instant. A planned purchase executes at the open of the first available candle at or after `scheduled_at`; at `executed_at`, it decreases cash by its gross amount and increases crypto quantity by its post-fee amount divided by that open price. An absent scheduled candle therefore shifts execution forward without rewriting `contributed_at`. No execution occurs after the timerange and capital without an eligible DCA slot remains cash.

Daily and weekly methods first aggregate only same-timestamp cash flows into one deployment bucket, then divide that bucket over eligible slots before the next strictly later bucket (or range end). The raw investor schedule still preserves each initial/monthly event and its kind separately. Immediate deploys each same-timestamp bucket when it arrives. There is intentionally no `monthly_dca` duplicate: monthly investor cash flows and immediate deployment are the single defined monthly-cash behavior. All methods receive the same contribution calendar and `total_contributions`, but can have different invested capital and cash balances.

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

## Auditable accounting equations

All symbols below are exact `Decimal` values; timestamps are UTC.  For purchase *i*, let
`Gᵢ` be `gross_contribution`, `f` the configured fee ratio, and `Pᵢ` the first eligible
candle's open. The engine uses these equations exactly:

- **Fee:** `Fᵢ = Gᵢ × f`.
- **Net amount invested:** `Nᵢ = Gᵢ − Fᵢ = Gᵢ × (1 − f)`.
- **Acquired quantity:** `Qᵢ = Nᵢ / Pᵢ`.
- **Cumulative quantity after i:** `Qcumᵢ = Σ Qⱼ`.
- **Cumulative gross contributions at time t:** `C(t) = Σ` investor events credited by t.
- **Cumulative fees:** `Fcumᵢ = Σ Fⱼ`.
- **Residual cash:** `Rᵢ = Rᵢ₋₁ + credited cash − Gᵢ`. Thus a fee is paid from the
  gross order and reduces asset received; it does not create a second cash debit.
- **Average entry price:** `Σ Gⱼ / Σ Qⱼ`. This is the *gross-cost basis per acquired
  unit*, so it includes purchase fees and is deliberately not the average execution price.
- **Final value:** `Vfinal = Qcum × Plast_close + Rfinal`.
- **Absolute profit:** `Vfinal − Cfinal`; **profit percentage:**
  `(Vfinal − Cfinal) / Cfinal`.

Raw portfolio drawdown is `max_t ((peak(V) − V(t)) / peak(V))`, where
`peak(V) = max_{u≤t} V(u)`. It is reported for transparency but deposits can change it.
The contribution-neutral drawdown instead applies the same equation to a time-weighted
unit value `U`. Before each contribution at a candle open, issue `contribution / Uopen`
units, where `Uopen = portfolio_open / units_outstanding` (and `Uopen = 1` for the first
contribution). Purchases issue no units. After the close, `Uclose = Vclose / units_outstanding`.
This removes investor cash-flow effects: a contribution cannot create a positive return or
make an existing drawdown better merely by arriving. The first candle-close valuation after the
first execution is the first observed peak. Therefore an initial purchase fee does **not** itself
create time-weighted drawdown; a later fee or price decline below that observed value does. This
convention applies equally to immediate, daily, and weekly deployment.

## Purchase-ledger artifacts

Every benchmark JSON contains `purchase_ledger`; its Decimal fields are strings, preserving
exact values for independent recomputation. `average_entry_price_exact` likewise preserves the
exact derived cost basis alongside the legacy numeric summary value. CSV artifacts are generated
only when `--output-dir` is supplied; then every benchmark also writes `<benchmark>-<pair>-purchase-ledger.csv` (with headers even if there were no
eligible buys). Each record includes contribution, scheduled, and execution timestamps;
execution price; gross, fee, and net amounts; acquired and cumulative quantity; cumulative
gross contributions and fees; residual cash; and close-marked portfolio value. The legacy
numeric `purchases` field remains for report compatibility.
