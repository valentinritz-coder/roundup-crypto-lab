# Architecture

## Execution boundary

During the initial phase, GitHub-hosted Ubuntu runners own all executable validation:

- Python lint and tests;
- Freqtrade installation;
- public market-data download;
- strategy discovery and configuration resolution;
- backtesting;
- look-ahead and recursive analysis;
- a short dry-run startup smoke test.

The runners are ephemeral. Generated logs and results are uploaded as workflow artifacts before the
runner disappears.

## Product boundary

Freqtrade owns market data access, indicator execution, backtesting, dry-run order simulation,
trade state, and eventual exchange integration.

This repository owns:

- the strategy source;
- exact-cent contribution accounting;
- bank CSV normalization;
- risk policy;
- contribution-aware reporting;
- tests and reproducibility metadata;
- GitHub Actions orchestration.

## Data flows

```text
Bank CSV -> normalize -> eligible payments -> exact roundups -> monthly contribution ledger

GitHub Action -> Kraken public candles -> deterministic strategy -> Freqtrade backtest
                                                       -> validation artifacts

Contribution ledger + trade ledger -> reporting layer
                               -> external cash vs P&L vs fees vs value
```

The contribution ledger and trade ledger remain distinct. A deposit is not profit.

## V1 exclusions

- continuous dry-run hosting inside GitHub Actions;
- direct bank connectivity;
- automatic fiat deposits;
- live exchange credentials;
- multi-exchange routing;
- tax reporting;
- portfolio optimization;
- parameter hyperoptimization;
- machine learning and LLM agents;
- Docker-based execution.
