# Unified scenario comparison

The all-strategy workflow produces one pair-specific comparison that contains active strategies and passive deployment methods under the same scenario.

## Scenario identity

A scenario identifier hashes the following canonical metadata:

- pair;
- timeframe;
- timerange;
- initial capital;
- monthly budget;
- contribution day;
- the complete contribution schedule and its own hash;
- fee ratio;
- capital mode;
- repository commit.

Rows are ranked together only when every field matches. A different fee, contribution date, monthly budget, pair, timerange, capital mode, or commit creates a different scenario group. The official workflow uses strict mode and fails unless active and passive rows share exactly one scenario.

## Capital modes

### Recurring monthly contributions

The investor contributes the initial capital at the timerange start and the configured monthly budget on the shared contribution schedule.

Passive alternatives are:

- `DailyDCA`: each available funding bucket is split across eligible daily purchases until the next contribution;
- `WeeklyDCA`: each bucket is split across the configured weekday until the next contribution;
- `MonthlyDCA`: each bucket is split across monthly deployment dates. With monthly funding, the contribution is normally deployed immediately because the next funding event begins the next bucket.

### One-shot capital

Only the initial contribution is credited. No later monthly cash flow is invented.

Passive alternatives are:

- `BuyAndHold`: immediate deployment of the full initial capital;
- `DailyDCA`;
- `WeeklyDCA`;
- `MonthlyDCA`.

## Two rankings

The report deliberately separates two questions.

### Strategy skill

Ranked by:

1. time-weighted return;
2. contribution-neutral drawdown;
3. deterministic method name ordering.

This view evaluates the method independently of investor cash-flow timing.

### Investor outcome

Ranked by:

1. final marked-to-market value;
2. absolute profit;
3. deterministic method name ordering.

The table also displays XIRR, fees, final cash, average deployed capital, utilization, and both drawdown definitions.

## Outputs

Each all-strategy artifact contains:

- `controlled-comparison.json`: active-only controlled result;
- `scenario-passive.json`: passive alternatives for the selected pair and capital mode;
- `unified-comparison.json`: grouped and ranked active/passive result;
- `unified-comparison.csv`: flat downstream table;
- `unified-summary.md`: GitHub Job Summary table.

## Pair scope

BTC/EUR and ETH/EUR are alternative pair-specific scenarios. They are never added together or described as a diversified portfolio. Multi-asset allocation, shared cash, rebalancing, and portfolio-level risk require a separate portfolio engine and are outside this comparison layer.
