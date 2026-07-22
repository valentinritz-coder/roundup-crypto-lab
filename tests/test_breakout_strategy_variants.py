import ast
import importlib
import sys
import types
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from roundup_crypto_lab.active_backtests import CapitalMode
from roundup_crypto_lab.freqtrade_active import (
    _repository_atr_stop_multiplier,
    _strategy_lifecycle,
    run_freqtrade_strategy,
    validate_pair_data_file,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan

STRATEGIES_DIR = Path("user_data/strategies").resolve()
STRATEGY_NAMES = (
    "RoundupBreakoutStrategy",
    "RoundupBreakoutTrendStrategy",
    "RoundupBreakoutAtrStrategy",
    "RoundupBreakoutAtrVolumeStrategy",
    "RoundupTrendPullbackStrategy",
    "RoundupConfirmedBreakoutStrategy",
    "RoundupVolatilitySqueezeStrategy",
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
        (frame["high"] - frame["low"]).rolling(timeperiod).mean()
    )
    talib_module = types.ModuleType("talib")
    talib_module.abstract = abstract
    monkeypatch.setitem(sys.modules, "talib", talib_module)
    monkeypatch.setitem(sys.modules, "talib.abstract", abstract)
    monkeypatch.syspath_prepend(str(STRATEGIES_DIR))
    for module in (*STRATEGY_NAMES, "_roundup_breakout_variants", "ExperimentalTrendBase"):
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


def test_all_strategies_import_and_process_synthetic_candles() -> None:
    for name in STRATEGY_NAMES:
        strategy = load(name)()
        assert strategy.timeframe == "4h"
        assert strategy.can_short is False
        assert strategy.startup_candle_count >= 120
        dataframe = strategy.populate_indicators(candles(), {})
        result = strategy.populate_entry_trend(dataframe, {})
        assert "breakout_high_20" in result
        assert "enter_long" in result


def test_second_generation_signals_are_explicitly_causal() -> None:
    pullback = inspect_source(load("RoundupTrendPullbackStrategy").populate_entry_trend)
    confirmed = inspect_source(load("RoundupConfirmedBreakoutStrategy").populate_entry_trend)
    squeeze = inspect_source(load("RoundupVolatilitySqueezeStrategy").populate_indicators)
    assert '"sma_100"].shift(5)' in pullback
    assert '"breakout_high_20"].shift(1)' in confirmed
    assert "rolling(100).quantile(0.20).shift(1)" in squeeze


def test_breakout_is_previous_twenty_candle_high() -> None:
    dataframe = load("RoundupBreakoutTrendStrategy")().populate_indicators(candles(), {})
    assert dataframe.loc[20, "breakout_high_20"] == dataframe.loc[:19, "high"].max()


def test_variant_entry_filters_and_tags() -> None:
    trend = load("RoundupBreakoutTrendStrategy")().populate_entry_trend
    atr = load("RoundupBreakoutAtrStrategy")().populate_entry_trend
    volume_strategy = load("RoundupBreakoutAtrVolumeStrategy")()
    assert "sma_50" in inspect_source(trend) and 'sma_100"].shift(1)' in inspect_source(trend)
    assert '0.25 * dataframe["atr_14"]' in inspect_source(atr)
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


def test_real_baseline_strategy_runs_through_recurring_cash_flow_bridge() -> None:
    # This spans two monthly contribution dates after the initial contribution,
    # while still executing the repository's real strategy implementation.
    rows = 500
    values = range(rows)
    frame = pd.DataFrame(
        {
            "open": [100 + value for value in values],
            "high": [101 + value for value in values],
            "low": [99 + value for value in values],
            "close": [100.5 + value for value in values],
            "volume": [10 + value for value in values],
        }
    )
    frame.insert(0, "date", pd.date_range("2026-01-01", periods=rows, freq="4h", tz="UTC"))
    # The final completed candle creates an entry signal.  It can only execute
    # at a later open, so this fixture proves no same-open use of its close.
    frame.loc[125, ["close", "high"]] = (1_000, 1_001)
    result = run_freqtrade_strategy(
        frame,
        InvestmentPlan("100", "40", "0", 15),
        "RoundupBreakoutStrategy",
        STRATEGIES_DIR,
        frame.iloc[120]["date"].to_pydatetime(),
        (frame.iloc[-1]["date"] + pd.Timedelta(hours=4)).to_pydatetime(),
        mode=CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS,
    )
    assert result["strategy"] == "RoundupBreakoutStrategy"
    assert result["signal_execution"] == "completed candle N signals execute at candle N+1 open"
    assert "contribution_ledger" in result and "equity_curve" in result
    assert len(result["contribution_ledger"]) >= 3
    start = frame.iloc[120]["date"]
    assert all(pd.Timestamp(row["timestamp"]) >= start for row in result["trades"])
    assert all(pd.Timestamp(row["timestamp"]) >= start for row in result["equity_curve"])
    assert all(pd.Timestamp(row["credited_at"]) >= start for row in result["contribution_ledger"])


def test_real_strategy_bridge_rejects_insufficient_warmup_history() -> None:
    frame = candles().iloc[:120].copy()
    frame.insert(0, "date", pd.date_range("2026-01-01", periods=len(frame), freq="4h", tz="UTC"))
    with pytest.raises(ValueError, match="insufficient warm-up history: need 120 candles"):
        run_freqtrade_strategy(
            frame,
            InvestmentPlan("100", "40", "0", 15),
            "RoundupBreakoutStrategy",
            STRATEGIES_DIR,
            frame.iloc[119]["date"].to_pydatetime(),
            (frame.iloc[-1]["date"] + pd.Timedelta(hours=4)).to_pydatetime(),
            mode=CapitalMode.ONE_SHOT_CAPITAL,
        )


@pytest.mark.parametrize(
    ("pair", "filename", "valid"),
    [
        ("BTC/EUR", "BTC_EUR-4h.feather", True),
        ("ETH/EUR", "ETH_EUR-4h.feather", True),
        ("BTC/EUR", "ETH_EUR-4h.feather", False),
        ("ETH/EUR", "BTC_EUR-4h.feather", False),
    ],
)
def test_pair_data_file_mapping(pair: str, filename: str, valid: bool) -> None:
    if valid:
        validate_pair_data_file(pair, Path(filename))
    else:
        with pytest.raises(ValueError, match="data file must be"):
            validate_pair_data_file(pair, Path(filename))


class _LifecycleStub:
    can_short = False
    trailing_stop = False
    stoploss = -0.12
    minimal_roi = {"0": 100.0}
    use_exit_signal = True
    use_custom_stoploss = False


def test_minimal_roi_uses_config_precedence_and_normalizes_disabled_value() -> None:
    strategy = _LifecycleStub()
    assert _strategy_lifecycle(strategy).fixed_stoploss == Decimal("-0.12")
    assert _strategy_lifecycle(strategy, {"minimal_roi": {"0": 100}}).fixed_stoploss == Decimal(
        "-0.12"
    )
    assert _strategy_lifecycle(strategy, {"minimal_roi": {"0": "100"}}).use_exit_signal is True


def test_minimal_roi_active_config_is_rejected_even_when_strategy_is_disabled() -> None:
    strategy = _LifecycleStub()
    for roi in ({"0": 0.05}, {"60": 0.03, "0": 0.10}):
        with pytest.raises(ValueError, match="does not support active minimal_roi exits"):
            _strategy_lifecycle(strategy, {"minimal_roi": roi})


def test_config_minimal_roi_takes_priority_over_strategy() -> None:
    strategy = _LifecycleStub()
    strategy.minimal_roi = {"0": 0.05}
    _strategy_lifecycle(strategy, {"minimal_roi": {"0": "100"}})
    with pytest.raises(ValueError, match="does not support active minimal_roi exits"):
        _strategy_lifecycle(_LifecycleStub(), {"minimal_roi": {"0": 0.05}})


def test_current_strategies_declare_supported_atr_custom_stops() -> None:
    for name in STRATEGY_NAMES:
        strategy = load(name)()
        assert _repository_atr_stop_multiplier(strategy) == Decimal("2")


def test_unrecognized_custom_stop_is_rejected_without_source_inspection() -> None:
    class UnknownCustomStop(_LifecycleStub):
        use_custom_stoploss = True

        def custom_stoploss(self) -> float:
            return -0.5

    with pytest.raises(ValueError, match="repository ATR custom_stoploss form"):
        _strategy_lifecycle(UnknownCustomStop())
