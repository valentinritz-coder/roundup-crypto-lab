# Short-delay MonthlyDCA protocol v1

This document freezes the first BTC/EUR campaign studying whether a tactical delay of at most seven calendar days can improve accumulation over immediate MonthlyDCA without recreating material cash drag.

The machine-readable source of truth is [`research/short_delay_dca_protocol.v1.json`](../research/short_delay_dca_protocol.v1.json). The Python validator rejects unsupported fields, parameters, strategies, and policy changes.

## Frozen scope

- Market: BTC/EUR.
- Source data: Kraken 4-hour candles.
- Control: unchanged MonthlyDCA, with the complete contribution invested immediately.
- Candidates: exactly the three rules below.
- Maximum delay: exactly seven calendar days from contribution availability.
- Every contribution is independent and is fully deployed by its own deadline.
- No sale, leverage, partial reserve, cross-cycle reserve, borrowing, future contribution, machine learning, parameter search, or threshold optimization is permitted.

## Decision clock and completed observations

Every decision occurs at `00:00 UTC` on a calendar day `t`.

A 4-hour candle timestamp is treated as its closing timestamp. UTC day `D` becomes a completed daily observation only when all six 4-hour candle closes ending at `D 04:00`, `08:00`, `12:00`, `16:00`, `20:00`, and `(D + 1) 00:00 UTC` are present. Its daily close is the close of the final candle ending at `(D + 1) 00:00 UTC`.

At decision timestamp `t`, the newest visible daily observation is therefore day `t - 1 day`. No current incomplete daily candle, later close, future high, or future low is visible.

A MonthlyDCA contribution dated `D` becomes available at `D 00:00 UTC`. Its first decision uses data completed no later than that timestamp.

If any observation required by a rule is missing, the fail-safe action is full deployment at the current decision timestamp. Missing data may not extend the waiting period.

Weekends are ordinary calendar days. Month and year boundaries do not reset or extend a pending contribution.

## Mathematical definitions

Let `C_d` be the completed UTC daily close for day `d`.

Let

`SMA7_d = (C_d + C_{d-1} + ... + C_{d-6}) / 7`.

### Control: MonthlyDCA

Deploy 100% at contribution availability.

### Candidate 1: negative 7-day return

At each decision day `t`, evaluate the latest completed day `d = t - 1`.

Delay while:

`C_d < C_{d-7}`.

Deploy immediately when the inequality is false, or force deployment at `D + 7 calendar days`.

### Candidate 2: below 7-day moving average

At each decision day `t`, evaluate the latest completed day `d = t - 1` against the seven completed closes preceding it:

`reference_SMA7_d = (C_{d-1} + ... + C_{d-7}) / 7`.

Delay while:

`C_d < reference_SMA7_d`.

Deploy on the first decision where the inequality is false, or force deployment at `D + 7 calendar days`.

The lagged window is intentional: the compared close is not also included in its own reference average.

### Candidate 3: confirmed short decline

At contribution availability, let `d = D - 1`. Delay only if both conditions hold:

`C_d < SMA7_d`

and

`SMA7_d < SMA7_{d-3}`.

Once delayed, deploy on the first decision day whose latest completed close is positive relative to the preceding completed close:

`C_d > C_{d-1}`.

Otherwise force deployment at `D + 7 calendar days`.

## Deterministic examples

### Immediate deployment

A contribution is available on July 10 at `00:00 UTC`. The latest completed close is July 9. Under the negative-return rule, if `C_July9 >= C_July2`, the full contribution is deployed on July 10.

### Re-evaluation

A contribution is delayed on July 10. At July 11 `00:00 UTC`, only the July 10 completed close has newly become visible. The rule is re-evaluated using that close and older completed observations. A July 11 intraday price may not be used.

### Forced deployment across a month boundary

A contribution available on January 28 that remains delayed is fully deployed on February 4 at `00:00 UTC`. The month boundary has no special meaning and does not create a new reserve.

### Missing observation

A contribution is delayed on July 10, but the completed July 10 daily observation cannot be formed from six 4-hour closes. At July 11 `00:00 UTC`, the entire contribution is deployed. The missing observation cannot justify waiting longer.

## Pseudocode

```text
for each contribution independently:
    D = contribution availability day
    if strategy is MonthlyDCA:
        deploy all at D

    evaluate initial signal using only days completed before D 00:00 UTC
    if required data is missing or signal is clear:
        deploy all at D

    for elapsed in 1..7:
        t = D + elapsed calendar days
        if elapsed == 7:
            deploy all at t
        else:
            evaluate using only daily observations completed before t 00:00 UTC
            if required data is missing or release condition is met:
                deploy all at t
```

The tests cover protocol drift, incomplete candles, missing 4-hour observations, immediate deployment, signal re-evaluation, positive-close release, weekends, month boundaries, and exact seven-day forced deployment.
