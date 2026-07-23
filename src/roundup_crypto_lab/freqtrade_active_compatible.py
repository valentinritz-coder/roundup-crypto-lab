"""Run the active bridge with Freqtrade backtesting custom-stop semantics."""

from __future__ import annotations

import pandas as pd

from roundup_crypto_lab import freqtrade_active as bridge
from roundup_crypto_lab.active_backtests import Candle
from roundup_crypto_lab.freqtrade_compatible_backtest import run_freqtrade_compatible_backtest


def strategy_decisions(*args, **kwargs):
    """Use the current analyzed ATR, matching DataProvider visibility in backtests."""
    frame = args[0]
    strategy_name = args[1]
    strategy_dir = args[2]
    tradable_balance_ratio = args[3]
    pair = args[4]
    start = args[5]
    end = args[6]
    config = args[7] if len(args) > 7 else kwargs.get("config")

    required = {"date", "open", "high", "low", "close", "volume"}
    if not required <= set(frame.columns):
        raise ValueError("OHLCV frame is missing required columns")
    strategy = bridge._load_strategy(strategy_name, strategy_dir)
    warmup = int(strategy.startup_candle_count)
    if len(frame[frame["date"] < start]) < warmup:
        raise ValueError(f"insufficient warm-up history: need {warmup} candles before timerange start")
    if not bridge.Decimal("0") < tradable_balance_ratio <= bridge.Decimal("1"):
        raise ValueError("tradable balance ratio must be in (0, 1]")

    metadata = {"pair": pair}
    analyzed = strategy.populate_indicators(frame.copy(), metadata)
    analyzed = strategy.populate_entry_trend(analyzed, metadata)
    analyzed = strategy.populate_exit_trend(analyzed, metadata)
    candles = []
    for index, row in enumerate(analyzed.itertuples()):
        timestamp = row.date.to_pydatetime().astimezone(bridge.UTC)
        if start <= timestamp < end:
            current_atr = analyzed.iloc[index].get("atr_14")
            atr = None if pd.isna(current_atr) else bridge.Decimal(str(current_atr))
            candles.append(
                Candle(
                    timestamp,
                    bridge.Decimal(str(row.open)),
                    bridge.Decimal(str(row.close)),
                    bridge.Decimal(str(row.high)),
                    bridge.Decimal(str(row.low)),
                    atr,
                )
            )

    decisions = {}
    for index in range(len(analyzed) - 1):
        next_at = analyzed.iloc[index + 1]["date"].to_pydatetime().astimezone(bridge.UTC)
        row = analyzed.iloc[index]
        if not start <= next_at < end:
            continue
        if row.get("exit_long", 0) == 1:
            decisions[next_at] = bridge.Action.SELL
        elif row.get("enter_long", 0) == 1:
            decisions[next_at] = bridge.Action.BUY
    return candles, decisions, bridge._strategy_lifecycle(strategy, config)


def main() -> None:
    bridge.strategy_decisions = strategy_decisions
    bridge.run_active_backtest = run_freqtrade_compatible_backtest
    original_builder = bridge.build_active_result

    def compatible_builder(result, **kwargs):
        kwargs["execution_model"] = "freqtrade-strategy-signals/backtest-custom-stop-v2"
        return original_builder(result, **kwargs)

    bridge.build_active_result = compatible_builder
    bridge.main()


if __name__ == "__main__":
    main()
