# Historical crypto campaign

The `Historical crypto campaign` workflow executes the frozen registry from
`config/historical-crypto-scenarios-v1.json` as a GitHub Actions matrix.

## Execution model

1. The prepare job validates the registry through the repository-owned parser.
2. The parser emits one deterministic matrix row per frozen scenario.
3. Each row calls the reusable `All strategy comparison` workflow.
4. Every scenario uploads its normal comparison artifacts plus
   `scenario-status.json`, even when an earlier step fails.
5. The final job downloads the scenario artifacts and produces a campaign status
   summary showing success, failure, or missing for all registered scenarios.

The initial registry contains four end-exclusive windows for BTC/EUR and ETH/EUR,
so one dispatch expands to eight isolated one-shot jobs.

## Reproducibility boundaries

The matrix takes pair, timeframe, timerange, capital mode, contribution terms, fee
ratio, scenario ID, and registry ID directly from the validated registry. The
workflow does not contain a second handwritten copy of those values.

BTC and ETH remain alternative pair-specific scenarios. The campaign does not add
their balances or describe them as a diversified portfolio.

Strategy signals and parameters are not changed by this workflow. It only
orchestrates the existing active, passive, differential, and unified comparison
pipeline.

## Failure behavior

Matrix `fail-fast` is disabled. A failed scenario does not cancel the remaining
scenarios. Each called workflow records a status file before uploading diagnostics,
and artifact names include the stable scenario ID plus the workflow run ID.

The final summary runs with `always()`. Missing artifacts are reported as missing
rather than silently omitted. Re-running failed matrix jobs uses GitHub Actions'
normal job rerun behavior.

## Cache behavior

Scenario jobs restore prepared Kraken data but do not save or mutate the shared
cache. Matrix concurrency is capped to two jobs to limit resource pressure while
still avoiding an eight-job serial procession worthy of a government form.
