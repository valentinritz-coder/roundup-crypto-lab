"""Shared, causal risk and exit logic for second-generation experiments."""

from __future__ import annotations

from datetime import datetime

import talib.abstract as ta
from freqtrade.strategy import Trade, stoploss_from_absolute
from pandas import DataFrame


class ExperimentalTrendBase:
    """Keep risk management identical so experiments differ chiefly at entry."""

    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    process_only_new_candles = True
    # Covers SMA200 plus the shifted 100-candle squeeze quantile.
    startup_candle_count = 201
    minimal_roi = {"0": 100.0}
    stoploss = -0.12
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    use_custom_stoploss = True
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["sma_20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["sma_50"] = ta.SMA(dataframe, timeperiod=50)
        dataframe["sma_100"] = ta.SMA(dataframe, timeperiod=100)
        dataframe["sma_200"] = ta.SMA(dataframe, timeperiod=200)
        dataframe["atr_14"] = ta.ATR(dataframe, timeperiod=14)
        # Every level used for an entry is known before that entry candle starts.
        dataframe["breakout_high_20"] = dataframe["high"].rolling(20).max().shift(1)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] < dataframe["sma_20"]) & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "close_below_sma20")
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs: object,
    ) -> float | None:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None
        atr = dataframe.iloc[-1]["atr_14"]
        if atr is None or atr <= 0:
            return None
        return stoploss_from_absolute(
            current_rate - 2.0 * float(atr),
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )
