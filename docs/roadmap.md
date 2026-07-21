# Roadmap

## Milestone 0: GitHub Actions laboratory

- Python package and unit-test workflow.
- Exact-cent roundups from generic CSV.
- Dry-run Freqtrade configuration.
- One causal 4h breakout strategy.
- Manual and change-triggered Freqtrade validation workflow.
- Backtest, look-ahead, recursive-analysis, and startup-smoke artifacts.

## Milestone 1: trustworthy research

- Pin Freqtrade to a reviewed release or immutable commit.
- Validate Kraken pair/timeframe availability.
- Stabilize backtest and bias-analysis workflows.
- Export reproducibility manifests.
- Add benchmark comparisons against BTC and ETH buy-and-hold.
- Add multiple non-overlapping historical evaluation windows.

## Milestone 2: contribution accounting

- Monthly contribution ledger.
- Idempotent bank imports.
- Contribution-aware portfolio replay.
- Reports separating deposits, withdrawals, fees, realized P&L, and unrealized P&L.
- Time-weighted return and XIRR.

## Milestone 3: continuous dry-run outside GitHub Actions

- Select a persistent host only after the workflows are stable.
- Alerts without public API exposure.
- Recovery and reconciliation tests.
- Several months of observation without parameter changes.

## Not scheduled

Real-money execution. It requires a separate decision after dry-run evidence exists.
