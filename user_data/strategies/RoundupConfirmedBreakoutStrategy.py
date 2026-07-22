"""Enter only after a complete candle confirms a 20-candle breakout."""

from ExperimentalTrendBase import ExperimentalTrendBase
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupConfirmedBreakoutStrategy(ExperimentalTrendBase, IStrategy):
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        level = dataframe["breakout_high_20"].shift(1)
        dataframe.loc[
            (dataframe["close"].shift(1) > dataframe["breakout_high_20"].shift(1))
            & (dataframe["close"] > level)
            & (dataframe["low"] >= level * 0.99)
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_50"] > dataframe["sma_100"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "confirmed_breakout_20")
        return dataframe
