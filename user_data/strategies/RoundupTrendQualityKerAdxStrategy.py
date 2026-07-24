"""Trend-following entry gated by efficiency and directional strength."""

from __future__ import annotations

import numpy as np
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupTrendQualityKerAdxStrategy(IStrategy):
    """Buy small breakouts only inside smooth, directional trends."""

    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 120

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
        directional_change = (dataframe["close"] - dataframe["close"].shift(20)).abs()
        travelled_distance = dataframe["close"].diff().abs().rolling(20).sum()
        dataframe["ker_20"] = directional_change / travelled_distance.replace(0, np.nan)
        dataframe["adx_14"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["sma_20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["sma_100"] = ta.SMA(dataframe, timeperiod=100)
        dataframe["prior_high_10"] = dataframe["high"].rolling(10).max().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["ker_20"] > 0.35)
            & (dataframe["adx_14"] > 25)
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["close"] > dataframe["prior_high_10"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "trend_quality_ker_adx_breakout")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            ((dataframe["ker_20"] < 0.20) | (dataframe["close"] < dataframe["sma_20"]))
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "trend_quality_lost_or_below_sma20")
        return dataframe
