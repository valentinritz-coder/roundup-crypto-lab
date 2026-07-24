import ast
from pathlib import Path

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "user_data/strategies"
BATCH_ONE = (
    "RoundupScientificControlBreakoutStrategy",
    "RoundupRiskAdjustedMomentumStrategy",
    "RoundupBullPullbackRsiStrategy",
    "RoundupDistanceReversionStrategy",
)


def source(strategy: str) -> str:
    return (STRATEGY_DIR / f"{strategy}.py").read_text(encoding="utf-8")


def test_batch_one_is_complete_and_ordered_in_comparison_registry() -> None:
    assert STRATEGY_ORDER[-4:] == BATCH_ONE
    assert len(STRATEGY_ORDER) == 11


def test_batch_one_sources_are_parseable_long_only_fixed_stop_strategies() -> None:
    for strategy in BATCH_ONE:
        text = source(strategy)
        ast.parse(text)
        assert f"class {strategy}(IStrategy):" in text
        assert 'timeframe = "4h"' in text
        assert "can_short = False" in text
        assert "use_custom_stoploss = False" in text
        assert "stoploss = -0.12" in text
        assert "trailing_stop = False" in text
        assert "shift(-" not in text
        assert ".iloc[" not in text


def test_scientific_control_uses_only_prior_channel_levels() -> None:
    text = source("RoundupScientificControlBreakoutStrategy")
    assert '.rolling(20).max().shift(1)' in text
    assert '.rolling(10).min().shift(1)' in text
    assert "talib" not in text
    assert "ta." not in text


def test_risk_adjusted_momentum_uses_return_and_realized_volatility() -> None:
    text = source("RoundupRiskAdjustedMomentumStrategy")
    assert '.shift(12)' in text
    assert '.rolling(20).std(ddof=0)' in text
    assert 'np.sqrt(12)' in text
    assert 'risk_adjusted_momentum' in text


def test_bull_pullback_waits_for_prior_oversold_recovery() -> None:
    text = source("RoundupBullPullbackRsiStrategy")
    assert 'ta.RSI(dataframe, timeperiod=2)' in text
    assert 'dataframe["rsi_2"].shift(1) < 10' in text
    assert 'dataframe["close"] > dataframe["close"].shift(1)' in text
    assert 'dataframe["sma_100"] > dataframe["sma_100"].shift(5)' in text


def test_distance_reversion_normalizes_pullback_and_requires_recovery_close() -> None:
    text = source("RoundupDistanceReversionStrategy")
    assert 'distance_atr' in text
    assert 'dataframe["atr_14"].replace(0, np.nan)' in text
    assert 'close_location' in text
    assert 'dataframe["distance_atr"] < -1.5' in text
    assert 'dataframe["close_location"] >= 0.60' in text
