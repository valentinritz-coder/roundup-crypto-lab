from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from roundup_crypto_lab.dca_controlled_comparison import _indicators, _schedule, write_outputs
from roundup_crypto_lab.investment_plan import InvestmentPlan


def test_indicator_frame_is_causal() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    dates = pd.date_range(start, periods=240, freq="4h")
    frame = pd.DataFrame({"date": dates, "open": range(240), "high": range(1, 241), "low": range(240), "close": range(1, 241), "volume": [1] * 240})
    first = _indicators(frame)
    changed = frame.copy()
    changed.loc[changed.index[-1], ["high", "low", "close"]] = [99999, 1, 99999]
    second = _indicators(changed)
    assert first.iloc[-1]["previous_close"] == second.iloc[-1]["previous_close"]
    assert first.iloc[-1]["long_ma"] == second.iloc[-1]["long_ma"]
    assert first.iloc[-1]["rolling_drawdown"] == second.iloc[-1]["rolling_drawdown"]


def test_schedule_is_deterministic_and_exact() -> None:
    plan = InvestmentPlan("40", "40", "0.0026", 1)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 4, 1, tzinfo=UTC)
    left = _schedule(plan, start, end)
    right = _schedule(plan, start, end)
    assert left == right
    assert sum((event.amount for event in left), Decimal("0")) == Decimal("160")


def test_output_writer_is_deterministic_except_manifest_timestamp(tmp_path: Path) -> None:
    payload = {
        "scenario": {"pair": "BTC/EUR"},
        "registry": {"registry_digest": "sha256:" + "a" * 64},
        "results": {"baselines": [], "pilots": []},
        "comparison": [{"method": "MonthlyDCA", "final_value": "40", "final_crypto_quantity": "1", "final_uninvested_cash": "0", "action_count": 1}],
    }
    registry = tmp_path / "registry.json"
    registry.write_text("{}\n")
    write_outputs(payload, tmp_path / "one", registry_path=registry, repository_commit="b" * 40)
    write_outputs(payload, tmp_path / "two", registry_path=registry, repository_commit="b" * 40)
    assert (tmp_path / "one/controlled-comparison.json").read_bytes() == (tmp_path / "two/controlled-comparison.json").read_bytes()
    assert (tmp_path / "one/controlled-comparison.csv").read_bytes() == (tmp_path / "two/controlled-comparison.csv").read_bytes()
    assert json.loads((tmp_path / "one/reproducibility-manifest.json").read_text())["registry_file_digest"].startswith("sha256:")


def test_output_writer_rejects_missing_comparison(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text("{}\n")
    with pytest.raises(KeyError):
        write_outputs({"scenario": {}, "registry": {}, "results": {}}, tmp_path / "out", registry_path=registry, repository_commit="c" * 40)
