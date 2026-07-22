import ast
import importlib
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

STRATEGIES_DIR = Path("user_data/strategies").resolve()
STRATEGY_NAMES = (
    "RoundupBreakoutStrategy",
    "RoundupBreakoutTrendStrategy",
    "RoundupBreakoutAtrStrategy",
    "RoundupBreakoutAtrVolumeStrategy",
)


@pytest.fixture(autouse=True)
def freqtrade_and_talib_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy_module = types.ModuleType("freqtrade.strategy")
    strategy_module.IStrategy = type("IStrategy", (), {})
    strategy_module.Trade = type("Trade", (), {})
    strategy_module.stoploss_from_absolute = lambda stop, **_: stop
    freqtrade_module = types.ModuleType("freqtrade")
    freqtrade_module.strategy = strategy_module
    monkeypatch.setitem(sys.modules, "freqtrade", freqtrade_module)
    monkeypatch.setitem(sys.modules, "freqtrade.strategy", strategy_module)

    abstract = types.ModuleType("talib.abstract")
    abstract.SMA = lambda frame, timeperiod: frame["close"].rolling(timeperiod).mean()
    abstract.ATR = lambda frame, timeperiod: (
        frame["high"] - frame["low"]
    ).rolling(timeperiod).mean()
    talib_module = types.ModuleType("talib")
    talib_module.abstract = abstract
    monkeypatch.setitem(sys.modules, "talib", talib_module)
    monkeypatch.setitem(sys.modules, "talib.abstract", abstract)
    monkeypatch.syspath_prepend(str(STRATEGIES_DIR))
    for module in (*STRATEGY_NAMES, "_roundup_breakout_variants"):
        sys.modules.pop(module, None)


def load(name: str) -> type:
    return getattr(importlib.import_module(name), name)


def candles() -> pd.DataFrame:
    values = range(130)
    return pd.DataFrame(
        {
            "open": [100 + value for value in values],
            "high": [101 + value for value in values],
            "low": [99 + value for value in values],
            "close": [100.5 + value for value in values],
            "volume": [10 + value for value in values],
        }
    )


def test_all_four_strategies_import_and_process_synthetic_candles() -> None:
    for name in STRATEGY_NAMES:
        strategy = load(name)()
        assert strategy.timeframe == "4h"
        assert strategy.can_short is False
        assert strategy.startup_candle_count >= 120
        dataframe = strategy.populate_indicators(candles(), {})
        result = strategy.populate_entry_trend(dataframe, {})
        assert "breakout_high_20" in result
        assert "enter_long" in result


def test_breakout_is_previous_twenty_candle_high() -> None:
    dataframe = load("RoundupBreakoutTrendStrategy")().populate_indicators(candles(), {})
    assert dataframe.loc[20, "breakout_high_20"] == dataframe.loc[:19, "high"].max()


def test_variant_entry_filters_and_tags() -> None:
    trend = load("RoundupBreakoutTrendStrategy")().populate_entry_trend
    atr = load("RoundupBreakoutAtrStrategy")().populate_entry_trend
    volume_strategy = load("RoundupBreakoutAtrVolumeStrategy")()
    assert "sma_50" in inspect_source(trend) and 'sma_100"].shift(1)' in inspect_source(trend)
    assert "0.25 * dataframe[\"atr_14\"]" in inspect_source(atr)
    assert "volume_sma_20" in inspect_source(volume_strategy.populate_indicators)
    assert "volume_sma_20" in inspect_source(volume_strategy.populate_entry_trend)


def inspect_source(function: object) -> str:
    import inspect

    return inspect.getsource(function)


def test_strategy_sources_do_not_reference_future_candles() -> None:
    for path in STRATEGIES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shift"
                and node.args
                and isinstance(node.args[0], ast.UnaryOp)
                and isinstance(node.args[0].op, ast.USub)
            ):
                pytest.fail(f"Negative shift in {path}")
