# Final passive DCA frequency research

This document records the completed BTC/EUR passive DCA frequency study and its
frozen operational decision. The machine-readable snapshot is stored in
[`passive-dca-frequency-final-decision.json`](passive-dca-frequency-final-decision.json).

## Official decision

> **MonthlyDCA confirmed.** Use monthly DCA as the passive benchmark and deploy
> each monthly contribution when it becomes available.

The exploratory evidence did not identify any lower-frequency schedule that
consistently beat MonthlyDCA. The independent confirmation window then ranked
monthly first under all four execution-cost profiles, including frictionless
control and hypothetical fixed-cost sensitivity.

This conclusion applies to the committed BTC/EUR experiment. It is not a claim
that monthly purchases must outperform on every future market path.

## Frozen protocol

The research compares four passive, unpiloted frequencies:

1. weekly;
2. monthly;
3. every two months;
4. quarterly.

All strategies receive the same monthly contributions. Every valid calendar
phase for the two-month and quarterly schedules is aggregated; no best phase is
selected. The final matrix uses these cost profiles, in order:

1. `frictionless-control-v1`;
2. `proportional-fee-v1`;
3. `proportional-plus-spread-v1`;
4. `hypothetical-fixed-cost-v1`.

The hypothetical fixed-cost profile adds 0.10 EUR per executed order. It is a
sensitivity analysis only and does not represent Kraken, Bitvavo or another
venue tariff.

## Data-quality amendment

The first exploratory attempt, run
[`30450539478`](https://github.com/valentinritz-coder/roundup-crypto-lab/actions/runs/30450539478),
identified a 36-hour BTC/EUR 4h candle gap from 2018-01-11 20:00 UTC to
2018-01-13 08:00 UTC. The frozen amendment excludes only the three windows that
contain this gap:

- `rolling-24m-6m-step`: `20180101-20200101`;
- `non-overlapping-24m`: `20180101-20200101`;
- `rolling-48m-12m-step`: `20180101-20220101`.

No candles are interpolated, forward-filled or substituted from another venue.
The amendment is committed in
`config/passive-dca-frequency-research-data-quality-amendment.json`.

## Archived runs

| Phase | GitHub Actions run | Commit | Coverage | Artifact digest |
| --- | --- | --- | ---: | --- |
| Exploratory | [`30451860585`](https://github.com/valentinritz-coder/roundup-crypto-lab/actions/runs/30451860585) | `4b9ba95766ada6133f65a8157046b2f0ce8c3321` | 48 scenarios / 336 strategy results | `sha256:f7ee140090faac1cde3f771f8f7a353cca855215b2adf5adda0fbb1e5204c074` |
| Confirmation | [`30452782223`](https://github.com/valentinritz-coder/roundup-crypto-lab/actions/runs/30452782223) | `4b9ba95766ada6133f65a8157046b2f0ce8c3321` | 4 scenarios / 28 strategy results | `sha256:8d10de5d286f0f07e0449798133e2777cbd0f33adf1d96986ff64a736b0dc9ac` |

The GitHub Actions artifacts expire on 2026-08-28. This document and its JSON
sidecar preserve the compact evidence and decision after those archives expire.

## Exploratory result

The exploratory evidence was mixed under the preregistered unanimity rule, but
MonthlyDCA won two of the three window sets in every cost profile.

| Cost profile | Non-overlapping 24m | Rolling 24m | Rolling 48m |
| --- | --- | --- | --- |
| Frictionless control | monthly | weekly, mixed | monthly |
| Proportional fee | monthly | weekly, mixed | monthly |
| Proportional + spread | monthly | weekly, mixed | monthly |
| Hypothetical fixed cost | monthly | quarterly, rejected | monthly |

Under the realistic `proportional-plus-spread-v1` profile, weekly exceeded
monthly by only 0.61 EUR in median terminal value on rolling 24-month windows
(1472.42 EUR versus 1471.81 EUR). The classification remained `mixed`, and
weekly lost on the non-overlapping 24-month and rolling 48-month sets.

The fixed-cost quarterly win on rolling 24-month windows was also classified
`rejected` because it was not robust across windows and calendar phases.

## Confirmation result

The confirmation holdout covers `20240101-20260101`. Monthly ranked first and
received the `primary control` classification in all four cost profiles.

| Cost profile | Monthly net terminal value | Confirmation winner |
| --- | ---: | --- |
| Frictionless control | 1118.55 EUR | monthly |
| Proportional fee | 1115.64 EUR | monthly |
| Proportional + spread | 1115.08 EUR | monthly |
| Hypothetical fixed cost | 1112.48 EUR | monthly |

### Realistic profile detail

| Rank | Frequency | Net terminal value | Difference vs monthly | Execution cost | Orders | Average waiting-cash age | Classification |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | monthly | 1115.08 EUR | 0.00 EUR | 3.10 EUR | 24 | 0.00 days | primary control |
| 2 | every two months | 1095.42 EUR | -19.66 EUR | 3.04 EUR | 12 | 15.24 days | rejected |
| 3 | weekly | 1093.48 EUR | -21.61 EUR | 3.10 EUR | 105 | 14.50 days | rejected |
| 4 | quarterly | 1084.18 EUR | -30.91 EUR | 2.97 EUR | 8 | 29.44 days | rejected |

The lower-frequency schedules saved very little execution cost relative to the
performance lost while monthly contributions waited in cash. Weekly generated
far more orders without improving the confirmation outcome.

## Interpretation boundary

The exploratory interpretation was frozen before the confirmation run. The
confirmation holdout supports that interpretation and must not now be reused to
select another frequency, tune thresholds or redefine the decision rule.

MonthlyDCA remains the primary passive benchmark for subsequent repository
research. Any future challenge to that benchmark must use a newly specified
question, protocol and holdout rather than repeated inspection of these results.

## Scope and limitations

- BTC/EUR only;
- 4h Kraken candles and committed historical windows;
- passive accumulation only, with no selling, indicators or market timing;
- results depend on the committed execution-cost assumptions;
- historical confirmation does not guarantee future investment performance.

The implementation and research task is tracked in
[#117](https://github.com/valentinritz-coder/roundup-crypto-lab/issues/117),
which is closed as completed.
