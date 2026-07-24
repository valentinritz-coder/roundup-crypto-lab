"""Causal short-horizon mean reversion inside an established bullish regime."""

from __future__ import annotations

import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupBullPullbackRsiStrategy(IStrategy):
    """Buy a confirmed rebound after an RSI2 oversold pullback above SMA100."""

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
        dataframe["sma_20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["sma_100"] = ta.SMA(dataframe, timeperiod=100)
        dataframe["rsi_2"] = ta.RSI(dataframe, timeperiod=2)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_100"] > dataframe["sma_100"].shift(5))
            & (dataframe["rsi_2"].shift(1) < 10)
            & (dataframe["close"] > dataframe["close"].shift(1))
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["close"] < dataframe["sma_20"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "bull_pullback_rsi2_recovery")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            ((dataframe["rsi_2"] > 55) | (dataframe["close"] >= dataframe["sma_20"]))
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "rsi2_or_sma20_mean_reversion")
        return dataframe
