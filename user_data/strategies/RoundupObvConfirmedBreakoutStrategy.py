"""Price breakout confirmed by on-balance volume and relative participation."""

from __future__ import annotations

import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupObvConfirmedBreakoutStrategy(IStrategy):
    """Buy a 20-bar breakout only when price and volume flow agree."""

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
        dataframe["obv"] = ta.OBV(dataframe)
        dataframe["obv_sma_20"] = ta.SMA(dataframe["obv"], timeperiod=20)
        dataframe["volume_sma_20"] = dataframe["volume"].rolling(20).mean()
        dataframe["relative_volume_20"] = (
            dataframe["volume"] / dataframe["volume_sma_20"].replace(0, float("nan"))
        )
        dataframe["sma_20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["sma_100"] = ta.SMA(dataframe, timeperiod=100)
        dataframe["prior_high_20"] = dataframe["high"].rolling(20).max().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["prior_high_20"])
            & (dataframe["obv"] > dataframe["obv_sma_20"])
            & (dataframe["relative_volume_20"] > 1.20)
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "obv_confirmed_breakout_20")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            ((dataframe["obv"] < dataframe["obv_sma_20"]) | (dataframe["close"] < dataframe["sma_20"]))
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "obv_or_sma20_breakdown")
        return dataframe
