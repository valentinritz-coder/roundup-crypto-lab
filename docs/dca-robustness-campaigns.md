# DCA robustness and confirmation campaigns

The controlled single-window comparison is the atomic experiment. This layer evaluates the same frozen DCA registry across reviewed collections of market windows without selecting a winner from one convenient start date.

## Campaign identity

A campaign commits the pair list, timeframe, investment plan, fees, window definitions, strategy registry path, classification policy path and repository commit. BTC/EUR and ETH/EUR remain separate research populations and are never combined as one portfolio.

Rolling 24-month windows provide regime coverage. Non-overlapping 24-month windows provide a less correlated view. Optional 48-month windows test long-horizon deployment behavior. Overlapping windows are explicitly disclosed as dependent observations and must not be interpreted as independent samples.

## Exploratory and confirmation phases

Every planned window has an explicit `phase`:

- `exploratory` windows may be used to identify survivors and failure modes;
- `confirmation` windows are holdouts and are reported separately;
- confirmation results must never be folded back into exploratory classification thresholds.

The survivor artifact contains only exploratory strategies classified as robust improvement, promising but cash-heavy, or regime-dependent. It is intended for later recurring research or live dry-run work, not as an automatic production promotion.

## Frozen classification policy

`config/dca-robustness-policy.json` contains versioned thresholds committed before campaign results are interpreted. The available classifications are:

- robust improvement;
- promising but cash-heavy;
- regime-dependent;
- unstable;
- inactive;
- rejected.

The engine does not infer thresholds from observed results. Missing, duplicate, unplanned or registry-incompatible scenarios fail the campaign rather than being silently discarded.

## Parameter neighborhoods

The campaign may preregister small adjacent parameter variants for selected strategy families. A variant registry is materialized from a deep copy of the frozen registry, so the original default remains unchanged. Unknown parameters are rejected. A historically stronger adjacent variant is reported as a variant and never replaces the frozen default automatically.

## Outputs

A successful aggregate produces:

- `dca-robustness-campaign.json`;
- `dca-robustness-campaign.csv`;
- `dca-robustness-report.md`;
- `dca-robustness-survivors.json`;
- `job-summary.md`;
- the full per-window controlled-comparison artifacts;
- campaign status and diagnostics even when execution fails.

The aggregate reports scenario coverage, profitability, final-value and quantity wins versus Monthly DCA, median and worst differences, XIRR and TWR where available, drawdowns, capital deployment, cash age, inactivity, ranks, fees and action-count distributions.

## Reproducibility

Campaign JSON, CSV, Markdown and survivor outputs contain no wall-clock timestamp. Identical campaign definitions, policy, registry digest, repository commit and scenario artifacts therefore produce byte-stable outputs.
