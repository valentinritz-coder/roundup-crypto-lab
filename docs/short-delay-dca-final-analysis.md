# Short-delay DCA final analysis

This analysis closes the bounded tactical-delay research sequence without reopening the confirmed passive-frequency decision.

## Benchmark

`monthly_dca_control` remains the exact control. Every challenger receives the same contribution timestamps and amounts, market observations, execution-cost profile and final valuation timestamp.

The analysis compares each challenger with the matching MonthlyDCA result at three levels:

- contribution-level execution price, waiting duration, release type, fees, spread and BTC quantity;
- window-level terminal value, BTC quantity, cash, costs, exposure, delay and drawdown diagnostics;
- rule-level median and worst-window differences, win rates, window-set breadth, forced-deployment dependence and continuous historical consistency.

Lower drawdown or lower market exposure cannot qualify a rule unless it also improves after-cost terminal value or BTC accumulation.

## Frozen adoption policy

The committed policy uses `proportional-plus-spread-v1` as the primary realistic research profile. A challenger qualifies only when every guardrail passes:

- median terminal-value improvement of at least 0.1%;
- at least 60% winning valid windows;
- positive evidence in all three committed multi-window sets;
- no worst-window deterioration beyond 0.5%;
- BTC improvement on at least 55% of matched contributions and a positive median BTC difference;
- forced deployment on no more than half of delayed contributions on average;
- positive terminal-value and BTC differences on the continuous historical complement.

Candidate parameters remain frozen. Rules are never combined, optimized or selected by a favorable start date.

## Final workflow

`Short-delay DCA final analysis` executes the complete 52-scenario matrix in one research run:

- 48 multi-window scenarios;
- four continuous historical-complement scenarios;
- four frozen strategies per scenario;
- 208 strategy results in total.

The workflow continues later scenarios inside a shard after one failure but blocks the final conclusion unless all 52 scenarios and all 208 strategy results are present and valid.

## Decision outcomes

The machine-readable conclusion selects exactly one outcome:

1. adopt one fully specified frozen short-delay rule; or
2. retain MonthlyDCA and end further pilotage research.

When no challenger passes every adoption guardrail, the required conclusion is:

> Retain MonthlyDCA, invest each monthly contribution immediately, and end further pilotage research.

The continuous path remains a historical complement, overlapping windows remain dependent observations, and historical execution is not a guarantee of future performance.
