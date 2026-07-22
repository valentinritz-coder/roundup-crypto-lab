## Research Philosophy

The purpose of this repository is not to discover strategies that merely look profitable.

It is to eliminate strategies that fail under rigorous testing.

Every hypothesis is assumed to be wrong until it survives repeated attempts to falsify it across different assets, market regimes, parameter ranges, fee assumptions, and unseen data.

A beautiful equity curve proves nothing.

A strategy that survives every attempt to break it has not been proven correct. It has merely earned the right to be studied further.

> “The first principle is that you must not fool yourself, and you are the easiest person to fool.”
>
> — Richard Feynman

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
  strictly parses look-ahead and recursive reports, records a machine-readable negative baseline, then performs a short dry-run startup smoke
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
user_data/config-lookahead.json Look-ahead-only market-pricing override
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

## Passive comparison references

The lab also has audited **Buy & Hold**, **Daily DCA**, and **Weekly DCA** references for the
same cached Kraken `BTC/EUR` and `ETH/EUR` 4-hour data and timerange as a backtest. They are a
separate deterministic Python engine, not Freqtrade strategies: purchases use the first eligible
candle open at or after `00:00 UTC`, fees apply to each buy, and there is no simulated sale.
Every passive method receives one shared investment plan: initial capital at the timerange start and the same monthly budget on the configured contribution day. Deployment timing (immediate, daily, weekly, or monthly) is separate from investor cash-flow timing, and DCA drawdown uses a contribution-neutral time-weighted equity curve. Run **Actions → Passive benchmarks** after
**Update Kraken data**, or use `python -m roundup_crypto_lab.passive_benchmarks`. See
[`docs/passive-benchmarks.md`](docs/passive-benchmarks.md) for the exact conventions, outputs,
limitations, and the warning that benchmarks do not establish future profitability.

## Breakout strategy experiments

The unchanged `RoundupBreakoutStrategy` is the comparison baseline. Experimental variants add a
trend filter, an ATR breakout-strength filter, and then a relative-volume filter; they are not
presumed profitable. See [`docs/breakout-strategy-variants.md`](docs/breakout-strategy-variants.md)
for the exact rules and reproducible comparison report.

Three second-generation, causal entry hypotheses are also available: a SMA20 pullback in an
established uptrend, a one-candle-confirmed breakout, and a breakout after a Bollinger-width
compression. They share the ATR14 two-ATR stop and SMA20 exit, remain long-only and are research
experiments rather than financial advice. See
[`docs/second-generation-strategies.md`](docs/second-generation-strategies.md). Run **Actions →
All strategy comparison** to backtest all seven strategies on one identical cached Kraken dataset,
timerange, configuration, fees, and starting capital.

Run each strategy separately on the same timerange (replace `$TIMERANGE` with the selected window):

```bash
freqtrade backtesting --config user_data/config.json --strategy RoundupBreakoutStrategy --timeframe 4h --timerange "$TIMERANGE"
freqtrade backtesting --config user_data/config.json --strategy RoundupBreakoutTrendStrategy --timeframe 4h --timerange "$TIMERANGE"
freqtrade backtesting --config user_data/config.json --strategy RoundupBreakoutAtrStrategy --timeframe 4h --timerange "$TIMERANGE"
freqtrade backtesting --config user_data/config.json --strategy RoundupBreakoutAtrVolumeStrategy --timeframe 4h --timerange "$TIMERANGE"
```

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

Run **Seed Kraken data** with the release tag, asset name, SHA-256 and a new seed version. The
pipeline is: **official archive → Seed imports and records gaps → Update repairs gaps and stale tail
from trades → strict validation → backtest / look-ahead / recursive / dry-run smoke**. Seed verifies
the archive and caches it when there are at least 480 4h warm-up candles plus 180 effective
validation days, but it deliberately preserves and reports any missing intervals. A green Seed
therefore means only that the historical archive was imported and cached—it does not mean the
market dataset is complete or current. **Update must be green before cache-backed Freqtrade
validation.**

Update restores the cache weekly and uses eight days of temporary public trades only when no
validation-relevant gap needs repair. Otherwise it requests an open-ended UTC Unix timerange from
one day before the earliest required repair point, then merges reconstructed closed candles.
Validation restores (but never saves) the newest cache. Release assets are user-retained source
inputs, caches hold prepared OHLCV only, and validation artifacts hold logs/reports—not market
data. Re-run Seed after cache eviction. Technical validation does not establish profitability.

The official reduced Kraken archive uses the seven columns `timestamp, open, high, low, close,
volume, trades` (with `trades` also called `count` in some headers). It does **not** include VWAP;
the importer retains the OHLC and volume fields and validates the trade count separately.

### Comparer les variantes breakout dans Actions

Après le merge des stratégies, lancez **Update Kraken data** si nécessaire, puis ouvrez
**Actions → Breakout strategy comparison → Run workflow**. Entrez par exemple
`20260123-20260722` et conservez `4h`. Le workflow consomme le cache Kraken préparé sans le
télécharger ni le réparer; une couverture insuffisante indique explicitement de lancer **Update
Kraken data** d'abord. Son artifact contient les quatre ZIP de backtest, le
`breakout-comparison.json`, les logs et les métadonnées; le Job Summary rend les métriques lisibles.
Ces résultats ne décrivent qu'un timerange et ne démontrent pas une rentabilité hors échantillon.
