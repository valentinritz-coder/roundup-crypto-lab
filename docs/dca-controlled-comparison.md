# Controlled DCA strategy comparison

The **Controlled DCA strategy comparison** workflow evaluates the four preregistered pilot DCA strategies and the Daily, Weekly and Monthly DCA controls under one strict scenario identity.

Each run accepts one pair only, `BTC/EUR` or `ETH/EUR`, so outputs from different markets are never merged into one comparison. The workflow uses only the prepared Kraken cache and fails when that cache is missing. It never downloads, repairs or extends market data.

## Fixed inputs

A run records the pair, timeframe, timerange, reviewed registry path and digest, initial capital, monthly budget, contribution day, fee ratio and repository commit. The committed registry remains the only accepted registry path for manual runs.

All methods receive the same contribution schedule and total contributed capital. Monthly DCA is the primary control; Daily and Weekly DCA remain secondary controls.

## Outputs

The artifact contains:

- one JSON result per method;
- decision-ledger and purchase-ledger CSV files;
- `controlled-comparison.json`;
- `controlled-comparison.csv`;
- `job-summary.md`;
- `reproducibility-manifest.json`;
- input and prepared-data validation diagnostics;
- `scenario-status.json`, even when execution fails.

The flat comparison exposes investor outcome, deployment quality and risk context as separate fields. It deliberately does not calculate a composite score or declare a winner.

## Interpretation boundary

A single timerange is exploratory evidence, not proof of superior future performance. Pilot defaults remain the preregistered values committed in the strategy registry. Changing them requires a new registry version and a new research decision before inspecting replacement results.
