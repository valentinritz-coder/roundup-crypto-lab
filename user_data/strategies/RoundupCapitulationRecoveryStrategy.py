"""Conditional recovery after an ATR-normalized, high-volume capitulation candle."""

from __future__ import annotations

import numpy as np
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupCapitulationRecoveryStrategy(IStrategy):
    """Buy only after a high-volume downside shock shows a confirmed recovery."""

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
        dataframe["atr_14"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["rsi_3"] = ta.RSI(dataframe, timeperiod=3)
        dataframe["volume_sma_20"] = dataframe["volume"].rolling(20).mean()
        dataframe["relative_volume_20"] = (
            dataframe["volume"] / dataframe["volume_sma_20"].replace(0, np.nan)
        )
        dataframe["down_move_atr"] = (
            dataframe["close"] - dataframe["close"].shift(1)
        ) / dataframe["atr_14"].replace(0, np.nan)
        candle_range = (dataframe["high"] - dataframe["low"]).replace(0, np.nan)
        dataframe["close_location"] = (dataframe["close"] - dataframe["low"]) / candle_range
        dataframe["previous_midpoint"] = (
            dataframe["high"].shift(1) + dataframe["low"].shift(1)
        ) / 2
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["down_move_atr"].shift(1) <= -1.50)
            & (dataframe["relative_volume_20"].shift(1) >= 1.50)
            & (dataframe["close_location"].shift(1) <= 0.25)
            & (dataframe["close"] > dataframe["previous_midpoint"])
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["rsi_3"] > dataframe["rsi_3"].shift(1))
            & (dataframe["close"] < dataframe["ema_20"])
            & (dataframe["rsi_3"] <= 55)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "capitulation_atr_volume_recovery")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            ((dataframe["close"] >= dataframe["ema_20"]) | (dataframe["rsi_3"] > 55))
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "capitulation_recovery_completed")
        return dataframe
