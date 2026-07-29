# Passive DCA frequency data-quality amendment

## Trigger

Exploratory workflow run `30450539478` applied the preregistered critical-gap rule to the prepared Kraken BTC/EUR 4-hour candles. Every cost profile rejected the same gap:

- previous candle: `2018-01-11T20:00:00+00:00`;
- next candle: `2018-01-13T08:00:00+00:00`;
- duration: 36 hours.

No execution-cost calculation, strategy schedule or phase aggregation caused these failures.

## Scientific treatment

The gap is not interpolated, forward-filled or replaced with candles from another venue. The amendment removes only the three market windows that contain it:

- `rolling-24m-6m-step`, `20180101-20200101`;
- `non-overlapping-24m`, `20180101-20200101`;
- `rolling-48m-12m-step`, `20180101-20220101`.

Each exclusion applies identically to all four cost profiles. Every other exploratory window and the isolated confirmation window remain unchanged.

## Amended matrix

The exploratory matrix changes from 60 scenarios to 48 scenarios:

- 8 rolling 24-month windows × 4 profiles;
- 2 non-overlapping 24-month windows × 4 profiles;
- 2 rolling 48-month windows × 4 profiles.

Each scenario still runs all seven preregistered passive strategy phases, producing 336 exploratory strategy results. Confirmation remains 4 scenarios and 28 strategy results.

## Workflow behavior

Use **Passive DCA frequency research amended** with `phase=exploratory` and the committed defaults.

The amended workflow validates the exact provenance, window changes and scenario counts before dispatch. Within a shard, one failed scenario is recorded but does not prevent later scenarios from running. The shard still finishes red and final aggregation remains blocked until the complete amended matrix succeeds.

## Interpretation boundary

Run `30450539478` is a data-quality diagnostic and a partial-computation artifact. Its 40 completed scenarios must not be ranked or used to select a DCA frequency because two valid rolling windows were skipped in every profile after the first invalid scenario stopped each affected shard.
