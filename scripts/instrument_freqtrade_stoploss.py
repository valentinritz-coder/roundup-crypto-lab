"""Instrument the pinned Freqtrade checkout for stoploss lifecycle diagnostics.

This script intentionally patches only the exact Freqtrade commit pinned by the
workflow. It records the strategy callback return, the internal Trade.stop_loss
before and after adjustment, and the close rate selected by the backtester.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(".freqtrade-src")
INTERFACE = ROOT / "freqtrade/strategy/interface.py"
BACKTESTING = ROOT / "freqtrade/optimize/backtesting.py"
HELPER = ROOT / "freqtrade/_stoploss_engine_trace.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match in {path}, found {count}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


HELPER.write_text(
    '''"""Optional JSONL tracing for Freqtrade stoploss engine internals."""\n\n'
    'from __future__ import annotations\n\n'
    'import json\n'
    'import os\n'
    'from pathlib import Path\n'
    'from typing import Any\n\n\n'
    'def engine_trace(event: str, **record: Any) -> None:\n'
    '    destination = os.environ.get("FREQTRADE_ENGINE_TRACE_PATH")\n'
    '    if not destination:\n'
    '        return\n'
    '    path = Path(destination)\n'
    '    path.parent.mkdir(parents=True, exist_ok=True)\n'
    '    payload = {"event": event, **record}\n'
    '    with path.open("a", encoding="utf-8") as stream:\n'
    '        stream.write(json.dumps(payload, sort_keys=True, default=str) + "\\n")\n',
    encoding="utf-8",
)

replace_once(
    INTERFACE,
    "from freqtrade.util import FtPrecise, dt_now, get_progress_tracker\n",
    "from freqtrade.util import FtPrecise, dt_now, get_progress_tracker\n"
    "from freqtrade._stoploss_engine_trace import engine_trace\n",
)

replace_once(
    INTERFACE,
    "        if after_fill and not self._ft_stop_uses_after_fill:\n",
    "        engine_trace(\n"
    "            \"adjust_start\",\n"
    "            strategy=self.__class__.__name__,\n"
    "            pair=trade.pair,\n"
    "            current_time=current_time,\n"
    "            current_rate=current_rate,\n"
    "            current_profit=current_profit,\n"
    "            low=low,\n"
    "            high=high,\n"
    "            after_fill=after_fill,\n"
    "            stop_loss_before=trade.stop_loss,\n"
    "            stop_loss_pct_before=trade.stop_loss_pct,\n"
    "            initial_stop_loss=trade.initial_stop_loss,\n"
    "            is_stop_loss_trailing=trade.is_stop_loss_trailing,\n"
    "        )\n"
    "        if after_fill and not self._ft_stop_uses_after_fill:\n",
)

replace_once(
    INTERFACE,
    "            # Sanity check - error cases will return None\n"
    "            if stop_loss_value_custom and not (\n",
    "            engine_trace(\n"
    "                \"callback_return\",\n"
    "                strategy=self.__class__.__name__,\n"
    "                pair=trade.pair,\n"
    "                current_time=current_time,\n"
    "                current_rate=(bound or current_rate),\n"
    "                current_profit=bound_profit,\n"
    "                low=low,\n"
    "                high=high,\n"
    "                after_fill=after_fill,\n"
    "                callback_return=stop_loss_value_custom,\n"
    "                stop_loss_before=trade.stop_loss,\n"
    "            )\n"
    "            # Sanity check - error cases will return None\n"
    "            if stop_loss_value_custom and not (\n",
)

replace_once(
    INTERFACE,
    "                trade.adjust_stop_loss(\n"
    "                    bound or current_rate, stop_loss_value, allow_refresh=after_fill\n"
    "                )\n",
    "                trade.adjust_stop_loss(\n"
    "                    bound or current_rate, stop_loss_value, allow_refresh=after_fill\n"
    "                )\n"
    "                engine_trace(\n"
    "                    \"adjust_result\",\n"
    "                    strategy=self.__class__.__name__,\n"
    "                    pair=trade.pair,\n"
    "                    current_time=current_time,\n"
    "                    current_rate=(bound or current_rate),\n"
    "                    current_profit=bound_profit,\n"
    "                    low=low,\n"
    "                    high=high,\n"
    "                    after_fill=after_fill,\n"
    "                    callback_return=stop_loss_value_custom,\n"
    "                    stop_loss_after=trade.stop_loss,\n"
    "                    stop_loss_pct_after=trade.stop_loss_pct,\n"
    "                    initial_stop_loss=trade.initial_stop_loss,\n"
    "                    is_stop_loss_trailing=trade.is_stop_loss_trailing,\n"
    "                    price_precision=trade.price_precision,\n"
    "                    precision_mode_price=trade.precision_mode_price,\n"
    "                )\n",
)

replace_once(
    BACKTESTING,
    "from freqtrade.util import FtPrecise, dt_now, get_progress_tracker\n",
    "from freqtrade.util import FtPrecise, dt_now, get_progress_tracker\n"
    "from freqtrade._stoploss_engine_trace import engine_trace\n",
)

replace_once(
    BACKTESTING,
    "        if is_short:\n"
    "            if stoploss_value < row[LOW_IDX]:\n"
    "                return row[OPEN_IDX]\n"
    "        else:\n"
    "            if stoploss_value > row[HIGH_IDX]:\n"
    "                return row[OPEN_IDX]\n",
    "        if is_short:\n"
    "            if stoploss_value < row[LOW_IDX]:\n"
    "                engine_trace(\"close_rate\", pair=trade.pair, current_time=trade.close_date_utc, exit_type=exit_.exit_type.value, trade_dur=trade_dur, open=row[OPEN_IDX], high=row[HIGH_IDX], low=row[LOW_IDX], close=row[CLOSE_IDX], stop_loss=trade.stop_loss, stop_loss_pct=trade.stop_loss_pct, is_stop_loss_trailing=trade.is_stop_loss_trailing, chosen_close_rate=row[OPEN_IDX], reason=\"stop_outside_candle\")\n"
    "                return row[OPEN_IDX]\n"
    "        else:\n"
    "            if stoploss_value > row[HIGH_IDX]:\n"
    "                engine_trace(\"close_rate\", pair=trade.pair, current_time=trade.close_date_utc, exit_type=exit_.exit_type.value, trade_dur=trade_dur, open=row[OPEN_IDX], high=row[HIGH_IDX], low=row[LOW_IDX], close=row[CLOSE_IDX], stop_loss=trade.stop_loss, stop_loss_pct=trade.stop_loss_pct, is_stop_loss_trailing=trade.is_stop_loss_trailing, chosen_close_rate=row[OPEN_IDX], reason=\"stop_outside_candle\")\n"
    "                return row[OPEN_IDX]\n",
)

replace_once(
    BACKTESTING,
    "            if is_short:\n"
    "                return min(row[HIGH_IDX], stop_rate)\n"
    "            else:\n"
    "                return max(row[LOW_IDX], stop_rate)\n\n"
    "        # Set close_rate to stoploss\n"
    "        return stoploss_value\n",
    "            if is_short:\n"
    "                close_rate = min(row[HIGH_IDX], stop_rate)\n"
    "            else:\n"
    "                close_rate = max(row[LOW_IDX], stop_rate)\n"
    "            engine_trace(\"close_rate\", pair=trade.pair, current_time=trade.close_date_utc, exit_type=exit_.exit_type.value, trade_dur=trade_dur, open=row[OPEN_IDX], high=row[HIGH_IDX], low=row[LOW_IDX], close=row[CLOSE_IDX], stop_loss=trade.stop_loss, stop_loss_pct=trade.stop_loss_pct, is_stop_loss_trailing=trade.is_stop_loss_trailing, chosen_close_rate=close_rate, reason=\"same_candle_trailing\")\n"
    "            return close_rate\n\n"
    "        # Set close_rate to stoploss\n"
    "        engine_trace(\"close_rate\", pair=trade.pair, current_time=trade.close_date_utc, exit_type=exit_.exit_type.value, trade_dur=trade_dur, open=row[OPEN_IDX], high=row[HIGH_IDX], low=row[LOW_IDX], close=row[CLOSE_IDX], stop_loss=trade.stop_loss, stop_loss_pct=trade.stop_loss_pct, is_stop_loss_trailing=trade.is_stop_loss_trailing, chosen_close_rate=stoploss_value, reason=\"stored_stop_loss\")\n"
    "        return stoploss_value\n",
)

print("Pinned Freqtrade stoploss instrumentation applied.")
