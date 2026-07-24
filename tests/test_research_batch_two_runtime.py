import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("freqtrade")
pytest.importorskip("talib")

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "user_data/strategies"
CASES = (
    ("RoundupObvConfirmedBreakoutStrategy", {"obv", "obv_sma_20", "relative_volume_20"}),
    ("RoundupTrendQualityKerAdxStrategy", {"ker_20", "adx_14", "prior_high_10"}),
    ("RoundupDonchianRetestStrategy", {"donchian_high_55", "donchian_retest_level"}),
    ("RoundupCapitulationRecoveryStrategy", {"down_move_atr", "previous_midpoint"}),
)


def synthetic_ohlcv() -> pd.DataFrame:
    index = np.arange(240, dtype=float)
    close = 100 + index * 0.04 + np.sin(index / 5) * 3
    open_ = close + np.sin(index / 3) * 0.4
    high = np.maximum(open_, close) + 1.2
    low = np.minimum(open_, close) - 1.2
    volume = 1000 + (index % 17) * 35
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-12-01", periods=len(index), freq="4h", tz="UTC"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


@pytest.mark.parametrize(("strategy_name", "expected_indicators"), CASES)
def test_batch_two_strategy_methods_execute_on_real_dataframe(
    strategy_name: str, expected_indicators: set[str]
) -> None:
    strategy_path = str(STRATEGY_DIR.resolve())
    if strategy_path not in sys.path:
        sys.path.insert(0, strategy_path)
    strategy_class = getattr(importlib.import_module(strategy_name), strategy_name)
    strategy = strategy_class({})

    analyzed = strategy.populate_indicators(synthetic_ohlcv(), {"pair": "BTC/EUR"})
    analyzed = strategy.populate_entry_trend(analyzed, {"pair": "BTC/EUR"})
    analyzed = strategy.populate_exit_trend(analyzed, {"pair": "BTC/EUR"})

    assert expected_indicators <= set(analyzed.columns)
    assert {"enter_long", "exit_long"} <= set(analyzed.columns)
    assert not analyzed.columns.duplicated().any()
    for signal in ("enter_long", "exit_long"):
        assert set(analyzed[signal].dropna().unique()) <= {1}
