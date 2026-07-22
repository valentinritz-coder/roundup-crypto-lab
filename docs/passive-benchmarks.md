# Passive benchmarks

This lab provides three deterministic, passive references for each independently evaluated pair
(`BTC/EUR` and `ETH/EUR`): **Buy & Hold**, **Daily DCA**, and **Weekly DCA**. They are not
Freqtrade strategies and do not combine the two assets into a portfolio.

## Data and execution convention

The engine reads only the already prepared Freqtrade Feather files in `user_data/data/kraken`; it
never downloads, patches, or merges market data. It accepts the same strict, end-exclusive
`YYYYMMDD-YYYYMMDD` timerange and fixed `4h` timeframe used for the cached backtests. It rejects
missing pairs, duplicate or unordered timestamps, gaps, invalid OHLCV data, and insufficient
coverage.

Every purchase is scheduled at `00:00 UTC` (on each date for daily DCA, or the configured weekday
for weekly DCA). It executes at the **open** of the first 4-hour candle at or after that instant.
Thus no completed candle close is used to choose an entry price, no interpolation is performed,
and an absent exact candle is deferred forward rather than filled from the past. Buy & Hold buys
on the first timerange candle. Holdings are marked at each candle close and at the final eligible
close; no simulated sale is made.

## Capital, fees, and performance

Buy & Hold invests only `initial_capital` once. DCA deliberately treats `daily_contribution` and
`weekly_contribution` as independent external cash flows, not as draws against that initial
capital. Its `total_contributions` therefore equals the contribution times its number of buys.
External contributions are never strategy profit.

For every buy, the fee convention is `net_contribution = gross_contribution × (1 - fee)` and
`quantity = net_contribution / execution_open`. Gross contributions remain the invested amount;
fees are reported separately. The default fee is `0.004` (0.4%). No sale fee is included because
the benchmarks never liquidate.

`profit_total = (final_portfolio_value - total_contributions) / total_contributions`. DCA also
records `portfolio_value`, `net_value` (portfolio minus cumulative contributions), and a
contribution-neutral time-weighted share value. Before each contribution, new shares are issued
for the net (post-fee) contribution at the current open-marked share value; drawdown of this share
value is reported as
`max_drawdown_time_weighted`. The raw portfolio drawdown is retained separately but is not the
headline DCA drawdown because deposits can distort it.

## Local use and outputs

```bash
python -m roundup_crypto_lab.passive_benchmarks \
  --timerange 20260123-20260722 \
  --output-json artifacts/benchmarks/passive-benchmarks.json \
  --output-dir artifacts/benchmarks
```

The JSON contains raw ratios (not percentages), six independent results, purchase histories, and
equity curves. Detail CSVs have explicit headers. `profit_factor`, `expectancy`, and `winrate`
are `null`, because passive holdings have no closed trades; human reports show these as N/A.
The existing comparator accepts the JSON through `--benchmark` and labels its rows
`category: benchmark`, separately from Freqtrade `category: strategy` rows.

## Limits

These are simple comparison references, not investment recommendations or evidence of future
profitability. They model no slippage, taxes, deposits fees, withdrawal fees, liquidity limits,
or sales. They remain dry-run research only, spot-only, long-only, with no leverage, borrowing,
shorting, staking, or derivatives.
