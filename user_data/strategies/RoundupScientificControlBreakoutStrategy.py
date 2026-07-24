"""Minimal causal Donchian breakout used as a scientific control."""

from __future__ import annotations

from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupScientificControlBreakoutStrategy(IStrategy):
    """Test a bare 20-bar breakout without trend, ATR, or volume filters."""

    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 30

    minimal_roi = {"0": 100.0}
    stoploss = -0.12
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    use_custom_stoploss = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["control_high_20"] = dataframe["high"].rolling(20).max().shift(1)
        dataframe["control_low_10"] = dataframe["low"].rolling(10).min().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["control_high_20"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "control_breakout_20")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] < dataframe["control_low_10"])
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "control_breakdown_10")
        return dataframe
