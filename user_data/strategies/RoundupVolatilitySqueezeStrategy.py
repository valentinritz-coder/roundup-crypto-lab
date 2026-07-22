"""Enter a causal breakout after a statistically defined volatility squeeze."""

from ExperimentalTrendBase import ExperimentalTrendBase
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupVolatilitySqueezeStrategy(ExperimentalTrendBase, IStrategy):
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        middle = dataframe["close"].rolling(20).mean()
        deviation = dataframe["close"].rolling(20).std(ddof=0)
        dataframe["bollinger_width"] = (4 * deviation) / middle
        dataframe["compression_threshold"] = (
            dataframe["bollinger_width"].rolling(100).quantile(0.20).shift(1)
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["bollinger_width"].shift(1) <= dataframe["compression_threshold"])
            & (dataframe["close"] > dataframe["breakout_high_20"])
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_50"] > dataframe["sma_100"])
            & (dataframe["bollinger_width"] > dataframe["bollinger_width"].shift(1))
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "volatility_squeeze_breakout")
        return dataframe
