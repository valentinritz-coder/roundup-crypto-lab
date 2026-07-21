# Freqtrade validation laboratory

## Pinned runtime

The validation workflow installs Freqtrade **2026.6** from tag `2026.6` and verifies that the
checked-out source is commit `b604e2fd70539f7f73d3c62c16ce0b155bbab319`. It installs the
release's `requirements.txt` and then installs the checked-out package with `--no-deps`, so the
reviewed dependency set is not silently re-resolved by the editable install.

## Commands run by the workflow

The runner uses Python 3.12 and runs the following commands, with stderr captured in the matching
artifact log. The workflow shell uses `bash -eo pipefail`; therefore a failed Freqtrade command
also fails its step rather than being hidden by `tee`.

```bash
freqtrade list-strategies --config user_data/config.json
freqtrade show-config --config user_data/config.json
freqtrade download-data --config user_data/config.json \
  --pairs BTC/EUR ETH/EUR --days 120 --timeframes 4h --dl-trades
freqtrade list-data --config user_data/config.json --timeframes 4h
freqtrade backtesting --config user_data/config.json \
  --strategy RoundupBreakoutStrategy --timeframe 4h \
  --export trades --export-filename artifacts/results/backtest.json
freqtrade lookahead-analysis --config user_data/config.json \
  --strategy RoundupBreakoutStrategy --timeframe 4h \
  --minimum-trade-amount 1 --targeted-trade-amount 10 \
  --lookahead-analysis-exportfilename artifacts/results/lookahead-analysis.csv
freqtrade recursive-analysis --config user_data/config.json \
  --strategy RoundupBreakoutStrategy --timeframe 4h \
  --startup-candle 120 240 480
```

Kraken does not expose backtest-grade historic OHLCV through Freqtrade. Its direct candle endpoint
is sufficient for dry-run and live operation but not for the historical downloader. The workflow
therefore requests public trades with `--dl-trades`; Freqtrade 2026.6 then automatically converts
them into the requested 4-hour candles because Kraken is marked as lacking historical OHLCV.
Downloading trades is slower and more memory-intensive than downloading candles, so the workflow
allows up to 90 minutes and deliberately limits the default window to the latest 120 days.

`list-data.txt` is the authoritative per-run record of the actual first and last candles produced;
market data may change between runs. The reproducibility manifest records the data source, the
trade-download mode, and each generated data-file path and size.

## Baseline and result interpretation

The pre-hardening baseline workflow completed successfully on 2026-07-21 at
[run 29828264698](https://github.com/valentinritz-coder/roundup-crypto-lab/actions/runs/29828264698)
for repository commit `65aeaab523a7050eece4efe25d10eff648a91e66`. GitHub records successful steps
for strategy/configuration, download, backtest, look-ahead analysis, recursive analysis, and the
smoke test. Its artifact is retained for 30 days.

That baseline is not evidence of a profitable strategy, a stable long-horizon result, or a bias
verdict: the old shell pipelines did not enable `pipefail`, and the prior smoke test accepted an
early exit. The first strict run,
[29847057217](https://github.com/valentinritz-coder/roundup-crypto-lab/actions/runs/29847057217),
correctly failed when the workflow attempted historical Kraken OHLCV without `--dl-trades`. This
failure exposed a real exchange-specific requirement instead of being hidden by the former shell
pipeline.

A successful run with the trade-download revision is required before using its backtest,
look-ahead, or recursive-analysis reports as evidence. In particular, `lookahead-analysis.txt`
must report no biased entry or exit columns and `recursive-analysis.txt` must explain no
instability across the 120, 240, and 480 startup-candle checks.

## ATR stop and dry-run smoke test

`RoundupBreakoutStrategy.custom_stoploss` reads Freqtrade's analyzed data for the current pair,
returns `None` when ATR is unavailable/non-positive, and otherwise calls
`stoploss_from_absolute(current_rate - 2 * ATR, current_rate, is_short=trade.is_short,
leverage=trade.leverage)`. With `can_short = False` and spot mode, this is a long-only stop below
the current rate. Freqtrade slices analyzed data to the evaluation point during backtesting, so the
last row used by the callback is not a future candle; dry-run uses the latest analyzed candle.
The strategy keeps the configured -12% stop as the fallback while ATR is warming up.

The smoke command is intentionally interrupted after 90 seconds. It succeeds only when the exit
is the expected timeout/interrupt **and** the log contains Freqtrade's transition to `RUNNING`;
an early clean exit and every other startup failure fail the job. No credentials are supplied, no
withdrawals are enabled, and the smoke database is an artifact only.

## Artifacts

Artifacts are uploaded even after a failed later step and contain command logs, exported backtest
trades, look-ahead CSV when produced, the temporary dry-run SQLite database when produced,
Freqtrade version and commit files, and `reproducibility.txt`. They do not make GitHub Actions a
continuous dry-run host.
