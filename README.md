# Roundup Crypto Lab

A deliberately small crypto swing-trading laboratory funded by:

- a fixed monthly contribution of **EUR 40**;
- the cents required to round eligible card payments up to the next euro.

The project separates three things humans routinely mix together and then celebrate in a chart:

1. external contributions;
2. trading profit and loss;
3. current portfolio value.

## Safety boundary

This repository starts in **dry-run only**.

- Spot trading only.
- No leverage.
- No margin or futures.
- No short selling.
- Maximum one open trade.
- No exchange withdrawal permission.
- No banking API connection in v1.
- Real transfers remain manual.

See [`docs/risk-policy.md`](docs/risk-policy.md).

## GitHub Actions first

No local Freqtrade or Docker installation is required for the initial research phase.

Two workflows are included:

- **Python CI** runs Ruff and the unit tests.
- **Freqtrade validation** installs Freqtrade on an ephemeral Ubuntu runner, downloads public
  Kraken trades, converts them into 4-hour candles, discovers the strategy, runs a backtest,
  checks look-ahead and recursive indicator behavior, then performs a short dry-run startup smoke
  test.

The Freqtrade workflow can be started manually from **Actions → Freqtrade validation → Run
workflow**. It also runs when the configuration, strategy, package, tests, or workflow change.

Every Freqtrade run uploads an artifact containing:

- command logs;
- the exported backtest;
- the temporary dry-run database when produced;
- the exact Freqtrade commit used;
- a reproducibility manifest.

GitHub Actions runners are temporary. The smoke test verifies startup only; it is not a
continuous paper-trading host.

The validated release, exact command lines, artifact contents, baseline run, and the limits of
the public Kraken history are documented in
[`docs/freqtrade-validation.md`](docs/freqtrade-validation.md).

## Repository layout

```text
src/roundup_crypto_lab/        Exact-cent roundup and contribution logic
user_data/config.json          Freqtrade dry-run configuration
user_data/strategies/          Freqtrade strategies
data/examples/                 Harmless example bank exports
docs/                          Architecture, risk policy, roadmap
codex/                         Bounded implementation tasks for Codex
.github/workflows/             Unit and Freqtrade validation pipelines
```

## Roundup calculation

The roundup module itself remains ordinary Python and is tested in GitHub Actions.

Example CSV:

```csv
date,transaction_id,description,amount
2026-07-01,payment-1,Boulangerie,-4.32
2026-07-01,payment-2,Station service,-63.17
```

Expected roundups: EUR 0.68 and EUR 0.83.

## Initial Freqtrade scope

- Kraken public trade data converted to OHLCV.
- BTC/EUR and ETH/EUR only.
- 4-hour candles.
- Spot and long-only.
- One open position maximum.
- Causal 20-candle breakout.
- SMA 100 trend filter.
- SMA 20 exit.
- ATR-based stop.

## First Codex mission

Use [`codex/001-bootstrap-and-validate.md`](codex/001-bootstrap-and-validate.md). Codex should
work through a focused pull request and rely on the workflow artifacts as evidence rather than
claiming that a command probably works because the YAML looks emotionally convincing.

## Reproducible Kraken seed data

Normal validation never downloads market history. Create a Release asset named
`kraken-btc-eth-eur-4h-seed.zip` for tag `kraken-data-seed-v1`; it must contain **only**
`XBTEUR_240.csv` and `ETHEUR_240.csv`. Kraken calls Bitcoin `XBT`, so `XBTEUR` maps to BTC/EUR;
`_240` is a 240-minute (4-hour) interval. Download the official Kraken OHLCVT quarterly files,
retain those exact two names, and create the reduced ZIP (for example `zip kraken-btc-eth-eur-4h-seed.zip XBTEUR_240.csv ETHEUR_240.csv`). In PowerShell calculate its digest with
`Get-FileHash .\kraken-btc-eth-eur-4h-seed.zip -Algorithm SHA256`, then create the GitHub Release
and upload that ZIP.

Run **Seed Kraken data** with the release tag, asset name, SHA-256 and a new seed version. It
imports the closed candles to a cache. **Update Kraken data** restores that cache weekly, downloads
eight days of temporary public trades, and merges only reconstructed closed candles. Validation
restores (but never saves) the newest cache. Release assets are user-retained source inputs, caches
hold prepared OHLCV only, and validation artifacts hold logs/reports—not market data. Re-run Seed
after cache eviction. Quarterly files may be incomplete or missing; inspect reported gaps. At least
480 4h warm-up candles plus 180 effective validation days are required. Technical validation does
not establish profitability.

The official reduced Kraken archive uses the seven columns `timestamp, open, high, low, close,
volume, trades` (with `trades` also called `count` in some headers). It does **not** include VWAP;
the importer retains the OHLC and volume fields and validates the trade count separately.
