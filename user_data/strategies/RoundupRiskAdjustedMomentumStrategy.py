"""Causal volatility-normalized momentum experiment."""

from __future__ import annotations

import numpy as np
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupRiskAdjustedMomentumStrategy(IStrategy):
    """Enter positive medium-horizon momentum only when volatility is orderly."""

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
        dataframe["atr_14"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["momentum_12"] = dataframe["close"] / dataframe["close"].shift(12) - 1
        dataframe["log_return"] = np.log(
            dataframe["close"] / dataframe["close"].shift(1)
        )
        dataframe["realized_vol_20"] = (
            dataframe["log_return"].rolling(20).std(ddof=0) * np.sqrt(12)
        )
        dataframe["risk_adjusted_momentum"] = dataframe["momentum_12"] / dataframe[
            "realized_vol_20"
        ].replace(0, np.nan)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["risk_adjusted_momentum"] > 1.0)
            & (dataframe["close"] > dataframe["sma_100"])
            & ((dataframe["high"] - dataframe["low"]) <= 2.5 * dataframe["atr_14"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "risk_adjusted_momentum_12_20")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["risk_adjusted_momentum"] < 0)
                | (dataframe["close"] < dataframe["sma_20"])
            )
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "momentum_lost_or_below_sma20")
        return dataframe
