"""Causal Donchian breakout followed by an orderly retest and recovery."""

from __future__ import annotations

import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupDonchianRetestStrategy(IStrategy):
    """Enter only after a 55-bar breakout survives a short retest."""

    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 140

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
        dataframe["sma_20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["sma_100"] = ta.SMA(dataframe, timeperiod=100)
        dataframe["donchian_high_55"] = dataframe["high"].rolling(55).max().shift(1)
        dataframe["donchian_low_20"] = dataframe["low"].rolling(20).min().shift(1)

        breakout = dataframe["close"] > dataframe["donchian_high_55"]
        breakout_level = dataframe["donchian_high_55"].where(breakout).ffill(limit=6)
        orderly_retest = (
            breakout_level.notna()
            & ~breakout
            & (dataframe["low"] <= breakout_level + 0.50 * dataframe["atr_14"])
            & (dataframe["close"] >= breakout_level - 0.50 * dataframe["atr_14"])
            & (dataframe["close"] <= breakout_level + 0.75 * dataframe["atr_14"])
        )
        recent_retest_level = breakout_level.where(orderly_retest).shift(1).ffill(limit=2)

        dataframe["donchian_breakout_level"] = breakout_level
        dataframe["donchian_retest_level"] = recent_retest_level
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe["donchian_retest_level"].notna()
            & dataframe["donchian_breakout_level"].notna()
            & (dataframe["donchian_retest_level"] == dataframe["donchian_breakout_level"])
            & (dataframe["close"] > dataframe["donchian_retest_level"])
            & (dataframe["close"] > dataframe["close"].shift(1))
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "donchian_55_orderly_retest_recovery")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close"] < dataframe["donchian_low_20"])
                | (dataframe["close"] < dataframe["sma_20"])
            )
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "donchian_retest_failed_or_below_sma20")
        return dataframe
