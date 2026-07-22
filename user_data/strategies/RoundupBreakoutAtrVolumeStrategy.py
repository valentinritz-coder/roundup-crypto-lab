"""ATR and relative-volume-filtered, causal 4h breakout experiment."""

from __future__ import annotations

from _roundup_breakout_variants import _RoundupBreakoutVariantMixin
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class RoundupBreakoutAtrVolumeStrategy(_RoundupBreakoutVariantMixin, IStrategy):
    """ATR-strength trend breakout that also requires above-average volume."""

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe["volume_sma_20"] = dataframe["volume"].rolling(20).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["breakout_high_20"] + 0.25 * dataframe["atr_14"])
            & (dataframe["close"] > dataframe["sma_100"])
            & (dataframe["sma_50"] > dataframe["sma_100"])
            & (dataframe["sma_100"] > dataframe["sma_100"].shift(1))
            & (dataframe["volume"] > dataframe["volume_sma_20"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "breakout_20_trend_atr_volume")
        return dataframe
