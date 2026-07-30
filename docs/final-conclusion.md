# Final project conclusion

## Status

**Research completed on 29 July 2026.**

The project is closed to further strategy and pilotage research. The repository remains available as a reproducible record of the hypotheses, protocols, implementations, workflows and negative results that led to the final decision.

## Final operational decision

> **Retain MonthlyDCA, invest each monthly contribution immediately, and end further pilotage research.**

The official strategy is therefore:

- one contribution becomes available each month;
- invest 100% of that contribution immediately;
- use no indicator or market-timing condition;
- maintain no tactical cash reserve;
- perform no sale, leverage, borrowing or discretionary override.

## Question investigated

The repository initially explored whether active crypto trading or alternative passive deployment rules could improve the outcome of a small recurring contribution plan. The research progressively narrowed to two questions:

1. Which passive DCA frequency is the most robust when each strategy receives identical contributions?
2. Can a strictly bounded tactical delay during a short decline improve MonthlyDCA after costs and the opportunity cost of cash?

## Main findings

### Active strategy research

The active breakout and second-generation strategy families did not establish robust superiority over the passive references. Attractive isolated backtests were not treated as sufficient evidence because the project required causal execution, identical cash flows, explicit costs, multiple windows and resistance to parameter or start-date selection.

### Passive frequency research

MonthlyDCA was retained as the passive benchmark. Less frequent deployment left contributed capital in cash for longer and created a meaningful opportunity cost. The benchmark therefore credits each monthly contribution and invests it immediately, without an indicator, sale or market-timing decision.

### Short-delay pilotage research

The final campaign compared MonthlyDCA with three frozen challengers:

1. delay after a negative seven-day return;
2. delay while price remained below its seven-day simple moving average;
3. delay after a confirmed short decline using price below the seven-day average and a negative short slope.

Every contribution had to be fully deployed within seven calendar days. The campaign covered:

- 52 committed scenarios;
- 208 strategy results;
- rolling 24-month windows;
- non-overlapping 24-month windows;
- rolling 48-month windows;
- one continuous historical complement;
- four frozen execution-cost profiles.

The complete matrix succeeded without missing or invalid scenarios.

Under the primary realistic cost profile, `proportional-plus-spread-v1`, no challenger qualified for adoption:

| Rank | Frozen challenger | Median terminal-value difference | Worst window | Window win rate | Long-horizon value difference |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | `confirmed_short_decline_delay` | -0.4474% | -0.5227% | 0.0% | -0.4094% |
| 2 | `below_7d_sma_delay` | -0.9463% | -1.6639% | 0.0% | -0.8268% |
| 3 | `negative_7d_return_delay` | -0.9811% | -1.6251% | 8.33% | -0.7103% |

The challengers also failed to demonstrate positive BTC accumulation across windows or on the continuous historical complement. Lower exposure or drawdown caused by holding cash was not accepted as evidence of superior return.

## Interpretation

The negative result is the conclusion, not a failed attempt to obtain one.

The tested delay rules sometimes bought individual contributions at a lower price. However, those gains were not broad or large enough to offset purchases made after rebounds and the opportunity cost of waiting cash. The strategies therefore added complexity without producing a stable and economically meaningful improvement over immediate MonthlyDCA.

The project deliberately does not replace the rejected seven-day rules with a newly invented three-day, EMA, acceleration or deceleration variant after observing the results. Such variants may be interesting hypotheses, but opening them now would violate the committed stopping rule and turn a completed falsification exercise into indefinite post-hoc optimization.

## Scientific boundaries

- Historical results do not guarantee future performance.
- Overlapping rolling windows are dependent observations.
- The continuous path is a historical complement, not an independent confirmation holdout.
- The previous passive-frequency confirmation period is not reinterpreted as a new holdout.
- BTC/EUR and the committed Kraken history define the tested market scope.
- The conclusion applies to the frozen strategies, costs, contribution rules and data conventions documented in this repository.

## Reproducibility references

- Final workflow: **Short-delay DCA final analysis**
- Final run: `30473443991`
- Final run commit: `991d92a3a04f752620a9d621ec6c12605814859f`
- Final decision policy: [`config/short-delay-dca-decision-policy.json`](../config/short-delay-dca-decision-policy.json)
- Frozen campaign: [`config/short-delay-dca-campaign.json`](../config/short-delay-dca-campaign.json)
- Machine-readable project decision: [`research/final-project-decision.json`](../research/final-project-decision.json)

## Closing statement

The repository achieved its purpose: it eliminated strategies that did not survive rigorous comparison and produced one explicit operational rule.

**MonthlyDCA remains the official benchmark. Each monthly contribution is invested immediately. Further pilotage research is closed.**
