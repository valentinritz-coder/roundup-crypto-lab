"""ATR-strength-filtered, causal 4h breakout experiment."""

from __future__ import annotations

from _roundup_breakout_variants import _RoundupBreakoutVariantMixin
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupBreakoutAtrStrategy(_RoundupBreakoutVariantMixin, IStrategy):
    """Trend breakout requiring the close to exceed the prior high by 0.25 ATR."""

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["breakout_high_20"] + 0.25 * dataframe["atr_14"])
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_50"] > dataframe["sma_100"])
            & (dataframe["sma_100"] > dataframe["sma_100"].shift(1))
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "breakout_20_trend_atr")
        return dataframe
