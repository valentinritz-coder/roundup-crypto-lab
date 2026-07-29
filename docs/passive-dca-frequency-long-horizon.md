# Passive DCA frequency long-horizon validation

This workflow adds one historical longitudinal complement to the completed passive
DCA frequency research. It does not reopen frequency optimization and it is not a
new independent confirmation.

## Question

> Does the confirmed MonthlyDCA decision remain coherent when the same frozen
> passive schedules are repeated continuously from 2018-07-01 through 2026-01-01?

The 90-month path begins after the documented January 2018 Kraken BTC/EUR 4h gap.
No candles are interpolated, forward-filled or substituted from another venue.

## Frozen matrix

The study reuses the dedicated passive-frequency registry and executes:

- weekly DCA on Monday;
- monthly DCA on the contribution day;
- every-two-month DCA, phases 0 and 1;
- quarterly DCA, phases 0, 1 and 2.

Every valid two-month and quarterly phase is retained as an equal-weight nuisance
replication. No best calendar phase is selected.

The four committed cost profiles produce four scenarios and 28 strategy results:

1. `frictionless-control-v1`;
2. `proportional-fee-v1`;
3. `proportional-plus-spread-v1`;
4. `hypothetical-fixed-cost-v1`.

## Workflow

Run **Passive DCA frequency long-horizon validation** with the committed defaults.
The action restores the prepared Kraken cache, executes one cost profile per shard,
then builds coverage, terminal analysis and longitudinal path diagnostics.

## Longitudinal outputs

The final artifact contains:

- the existing deterministic coverage reports;
- the existing phase-aggregated frequency analysis;
- `passive-frequency-long-horizon-analysis.json`;
- `passive-frequency-long-horizon-conclusion.json`;
- `passive-frequency-long-horizon-summary.csv`;
- `passive-frequency-long-horizon-trajectory.csv`;
- `long-horizon-summary.md`.

For every cost profile and phase-aggregated frequency, the dedicated summary records:

- final net value;
- final BTC quantity and final cash;
- total execution cost and order count;
- final difference versus MonthlyDCA;
- average capital deployment ratio;
- proportion of 4h observations spent below MonthlyDCA;
- worst and best intermediate difference versus MonthlyDCA;
- longest consecutive period below MonthlyDCA;
- terminal dispersion across nuisance phases.

The trajectory CSV retains the final 4h observation of each calendar month. Path
statistics use every 4h observation.

## Interpretation boundary

This path has already been observed during previous research. The conclusion may
therefore report only whether it is historically consistent with the confirmed
MonthlyDCA decision. It cannot claim a new holdout confirmation or prove future
performance over a personal 10- to 15-year investment horizon.

One long path also cannot replace the existing 24- and 48-month multi-window
analysis. The two views answer different questions: local robustness and cumulative
historical consistency.
