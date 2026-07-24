import ast
from pathlib import Path

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "user_data/strategies"
BATCH_TWO = (
    "RoundupObvConfirmedBreakoutStrategy",
    "RoundupTrendQualityKerAdxStrategy",
    "RoundupDonchianRetestStrategy",
    "RoundupCapitulationRecoveryStrategy",
)


def source(strategy: str) -> str:
    return (STRATEGY_DIR / f"{strategy}.py").read_text(encoding="utf-8")


def test_batch_two_is_complete_and_ordered_in_comparison_registry() -> None:
    assert STRATEGY_ORDER[-4:] == BATCH_TWO
    assert len(STRATEGY_ORDER) == 15


def test_batch_two_sources_are_parseable_long_only_fixed_stop_strategies() -> None:
    for strategy in BATCH_TWO:
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


def test_obv_breakout_uses_prior_price_and_current_volume_confirmation() -> None:
    text = source("RoundupObvConfirmedBreakoutStrategy")
    assert 'ta.OBV(dataframe)' in text
    assert 'dataframe["high"].rolling(20).max().shift(1)' in text
    assert 'dataframe["relative_volume_20"] > 1.20' in text
    assert 'dataframe["obv"] > dataframe["obv_sma_20"]' in text


def test_trend_quality_uses_kaufman_efficiency_and_adx() -> None:
    text = source("RoundupTrendQualityKerAdxStrategy")
    assert 'dataframe["close"].shift(20)' in text
    assert '.diff().abs().rolling(20).sum()' in text
    assert 'ta.ADX(dataframe, timeperiod=14)' in text
    assert 'dataframe["ker_20"] > 0.35' in text
    assert 'dataframe["adx_14"] > 25' in text


def test_donchian_retest_requires_prior_channel_break_and_later_recovery() -> None:
    text = source("RoundupDonchianRetestStrategy")
    assert 'dataframe["high"].rolling(55).max().shift(1)' in text
    assert '.where(breakout).ffill(limit=6)' in text
    assert '.where(orderly_retest).shift(1).ffill(limit=2)' in text
    assert 'dataframe["close"] > dataframe["donchian_retest_level"]' in text


def test_capitulation_recovery_uses_prior_shock_and_current_confirmation() -> None:
    text = source("RoundupCapitulationRecoveryStrategy")
    assert 'dataframe["down_move_atr"].shift(1) <= -1.50' in text
    assert 'dataframe["relative_volume_20"].shift(1) >= 1.50' in text
    assert 'dataframe["close_location"].shift(1) <= 0.25' in text
    assert 'dataframe["close"] > dataframe["previous_midpoint"]' in text
