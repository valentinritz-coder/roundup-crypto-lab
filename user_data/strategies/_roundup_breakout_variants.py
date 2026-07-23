"""Shared causal building blocks for the experimental breakout variants.

This module intentionally exposes no Freqtrade strategy class.  The three public
strategy modules remain individually discoverable and simple to compare.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import talib.abstract as ta
from freqtrade.strategy import Trade, stoploss_from_absolute
from pandas import DataFrame


def _iso(value: object) -> str | None:
    """Return a stable timestamp representation for diagnostic traces."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _append_stoploss_trace(path: str, record: dict[str, object]) -> None:
    """Append one JSON record without affecting normal strategy execution."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")


class _RoundupBreakoutVariantMixin:
    """Common indicators, exit, and ATR stoploss used by each experiment."""

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
        dataframe["atr_14"] = ta.ATR(dataframe, timeperiod=14)
        # Shift by one candle so the current high cannot define its own breakout.
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
        row = dataframe.iloc[-1]
        atr = row["atr_14"]
        if atr is None or atr <= 0:
            return None

        absolute_stop = current_rate - (2.0 * float(atr))
        returned_stop = stoploss_from_absolute(
            absolute_stop,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

        trace_path = os.environ.get("ROUNDUP_STOPLOSS_TRACE_PATH")
        if trace_path:
            _append_stoploss_trace(
                trace_path,
                {
                    "strategy": self.__class__.__name__,
                    "pair": pair,
                    "trade_open_timestamp": _iso(getattr(trade, "open_date_utc", None)),
                    "trade_open_rate": float(trade.open_rate),
                    "callback_timestamp": _iso(current_time),
                    "after_fill": bool(after_fill),
                    "current_rate": float(current_rate),
                    "current_profit": float(current_profit),
                    "analyzed_candle_timestamp": _iso(row.get("date")),
                    "atr": float(atr),
                    "absolute_stop_price": float(absolute_stop),
                    "returned_stoploss": float(returned_stop),
                },
            )

        return returned_stop
