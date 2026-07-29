# Passive DCA frequency campaign

This campaign compares only passive, buy-only deployment frequencies under committed execution-cost profiles.

## Strategy matrix

The dedicated registry contains exactly seven strategies:

- weekly DCA on the preregistered Monday convention;
- monthly DCA on the investment-plan contribution day;
- every-two-month DCA with phase offsets 0 and 1;
- quarterly DCA with phase offsets 0, 1, and 2.

There are no indicators, reserve tiers, amount multipliers, selling rules, or active strategies. Every strategy receives the same monthly contribution schedule and the same total contributed capital.

Calendar phases for intervals longer than one month are nuisance replications. They are retained with equal weight and are never searched, optimized, or ranked by their best outcome.

## Cost profiles

The initial campaign runs every research window under:

- `frictionless-control-v1`;
- `proportional-fee-v1`;
- `proportional-plus-spread-v1`.

All profiles are loaded from `config/execution-cost-profiles`. The proportional-plus-spread profile is a research assumption, not a claim about a venue or historical tariff.

## Research windows

Exploratory windows cover 2018 through the end of 2023 using rolling and non-overlapping 24-month windows and rolling 48-month windows. The 2024–2025 period is reserved for the confirmation phase, so exploratory and confirmation dates do not overlap.

The committed matrix contains:

- 45 exploratory scenarios, each with seven frequency results;
- 3 confirmation scenarios, each with seven frequency results.

BTC/EUR and the prepared Kraken 4-hour candles are the only market inputs in the first campaign.

## Workflow

Run `.github/workflows/passive-dca-frequency-campaign.yml` with `workflow_dispatch` and select either `exploratory` or `confirmation`.

The workflow:

1. validates the campaign, policy, dedicated strategy registry, and all cost profiles;
2. materializes and shards the deterministic window × cost-profile plan;
3. restores the prepared Kraken cache;
4. executes the complete seven-strategy passive matrix for each scenario;
5. writes paths and manifests containing the cost profile, frequency, and phase offset;
6. uploads every shard plus a compact aggregate coverage index and GitHub job summary.

The aggregate job deliberately performs no winner selection. Statistical phase aggregation, cost decomposition, and frequency ranking belong to issue #116.

## Reproducibility

Each scenario records the repository commit, campaign identity, registry digest, cost-profile digest, pair, timeframe, timerange, research phase, window set, and contribution assumptions. Per-strategy manifests additionally record the frequency family and phase offset.
