"""Enter a causal SMA20 pullback within an established uptrend."""

from ExperimentalTrendBase import ExperimentalTrendBase
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupTrendPullbackStrategy(ExperimentalTrendBase, IStrategy):
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["sma_50"] > dataframe["sma_100"])
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_100"] > dataframe["sma_100"].shift(5))
            & (dataframe["close"].shift(1) <= dataframe["sma_20"].shift(1) * 1.01)
            & (dataframe["close"].shift(1) >= dataframe["sma_50"].shift(1) * 0.98)
            & (dataframe["close"] > dataframe["sma_20"])
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "trend_pullback_sma20")
        return dataframe
