# Passive benchmarks

This lab compares **Buy & Hold (immediate)**, **Daily DCA**, **Weekly DCA**, and **Monthly DCA** for each independently evaluated pair. They are deterministic passive references, not Freqtrade strategies and not a multi-asset portfolio.

## One investor plan, separate deployment

Every result is funded by the same immutable investment plan: `initial_capital`, `monthly_budget`, `fee_ratio`, and `contribution_day`. The initial capital arrives at the start of the strict `YYYYMMDD-YYYYMMDD` timerange. Monthly cash flows occur at `00:00 UTC` on `contribution_day`; when a month lacks that day, the event is on its last calendar day. The range is **start-inclusive and end-exclusive**, so only events in `[start, end)` occur. This makes partial months and leap years deterministic.

Cash-flow timing is never rewritten to match market data. Each event remains in JSON metadata and purchase records as `contributed_at`. A deployment may execute at the first candle at or after its scheduled instant when the exact candle is absent; that later `executed_at` does not change the investor contribution date. Immediate and monthly deployment buy when cash arrives. Daily and weekly deployment split each cash flow exactly across eligible daily or configured-weekday schedule slots until the next cash flow (or range end). Therefore every deployment receives and invests exactly the same total capital, without silently creating extra money.

## Data, fees, and performance

The engine reads prepared Kraken Feather data only, using the `4h` timeframe. Purchases use the first eligible candle **open** and final holdings are marked at the final eligible close; it simulates no sale. Values are calculated with `Decimal` before JSON serialization. For each purchase, `net_contribution = gross_contribution × (1 - fee)` and `quantity = net_contribution / execution_open`. External contributions are not strategy profit.

JSON metadata records the full contribution schedule, initial capital, monthly budget, contribution day, total contributions, and each result's deployment method. `profit_total = (final_value - total_contributions) / total_contributions`; DCA headline drawdown uses the contribution-neutral time-weighted curve.

## Local use

```bash
python -m roundup_crypto_lab.passive_benchmarks \
  --timerange 20260123-20260722 \
  --initial-capital 200 --monthly-budget 40 --contribution-day 23 \
  --output-json artifacts/benchmarks/passive-benchmarks.json \
  --output-dir artifacts/benchmarks
```

`--daily-contribution` and `--weekly-contribution` have been removed intentionally: use the single `--monthly-budget` plan input instead. The lab remains dry-run, spot-only, long-only research; it models no slippage, taxes, deposit fees, withdrawal fees, liquidity limits, or sales.
