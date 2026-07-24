"""Causal ATR-normalized distance-to-mean reversion experiment."""

from __future__ import annotations

import numpy as np
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupDistanceReversionStrategy(IStrategy):
    """Buy an unusually deep pullback when the slower bullish regime remains intact."""

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
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["sma_100"] = ta.SMA(dataframe, timeperiod=100)
        dataframe["atr_14"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["distance_atr"] = (
            dataframe["close"] - dataframe["ema_20"]
        ) / dataframe["atr_14"].replace(0, np.nan)
        candle_range = (dataframe["high"] - dataframe["low"]).replace(0, np.nan)
        dataframe["close_location"] = (dataframe["close"] - dataframe["low"]) / candle_range
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_100"] > dataframe["sma_100"].shift(5))
            & (dataframe["distance_atr"] < -1.5)
            & (dataframe["close_location"] >= 0.60)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "distance_below_ema20_atr_recovery")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            ((dataframe["distance_atr"] >= 0) | (dataframe["close"] >= dataframe["ema_20"]))
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "distance_reverted_to_ema20")
        return dataframe
