# Passive DCA frequency analysis

The passive-frequency campaign ranks frequencies only after all calendar phases
for the same market window have been combined as equal-weight nuisance
replications.

The analysis keeps three levels separate:

1. **Phase-level results** preserve every weekly, monthly, two-month and
   quarterly execution schedule.
2. **Window-frequency aggregates** combine the valid phases for one frequency,
   cost profile and market window.
3. **Frequency summaries** combine those window aggregates within one research
   phase, cost profile and window set.

A calendar phase is never selected because it produced the best historical
result.

## Ranking rule

Frequencies are ranked independently for every pair, research phase, cost
profile and window set. The deterministic ordering is:

1. higher median net terminal value across phase-aggregated windows;
2. higher worst-window terminal value;
3. lower maximum phase dispersion;
4. the preregistered frequency order as a final deterministic tie-breaker.

Overlapping and non-overlapping window sets remain separate. Exploratory and
confirmation results are never combined.

## Diagnostic decomposition

For every frequency, the report separates:

- the matching frictionless terminal value for the same phase and market path;
- the net terminal value under the selected cost profile;
- terminal impact attributable to execution costs;
- explicit fees and estimated spread cost;
- timing advantage or disadvantage versus MonthlyDCA before costs;
- differential cost impact versus MonthlyDCA;
- average cash balance and capital deployment ratio;
- average and maximum contribution-to-deployment age;
- contribution-neutralized drawdown;
- phase dispersion and the worst observed phase.

The frictionless peer is the gross timing counterfactual. It is more informative
than simply adding execution-time fees back to the final result because it
captures the downstream value of the quantity that costs prevented from being
acquired.

## Artifacts

A successful campaign aggregate contains:

- `passive-frequency-analysis.json`;
- `passive-frequency-phase-level.csv`;
- `passive-frequency-window-summary.csv`;
- `passive-frequency-summary.csv`;
- `passive-frequency-cost-decomposition.csv`;
- `passive-frequency-rankings.csv`;
- `passive-frequency-classification.json`;
- `job-summary.md`.

The classification artifact follows
`passive-dca-frequency-classification/v1`. MonthlyDCA remains the primary
benchmark and is labelled as the control rather than classified as an
improvement over itself.

## Statistical disclosure

Calendar phases within the same market window are nuisance replications, not
independent market observations. Overlapping rolling windows are also not
independent. The output therefore preserves the window-set identity and avoids
turning replicated schedules into an inflated sample size.
