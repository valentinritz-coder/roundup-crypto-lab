import json
from pathlib import Path

import pytest

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.one_shot_diagnostic import _first_trade_difference, combine


def test_first_trade_difference_reports_count_and_context() -> None:
    native = [{"entry_timestamp": "a"}, {"entry_timestamp": "b"}]
    adapter = [{"entry_timestamp": "a"}]

    result = _first_trade_difference(native, adapter)

    assert result["first_divergent_index"] == 1
    assert result["native_trade_count"] == 2
    assert result["adapter_trade_count"] == 1
    assert result["native_trade"] == {"entry_timestamp": "b"}
    assert result["adapter_trade"] is None


def _document(strategy: str, status: str = "passed") -> dict[str, object]:
    return {
        "schema_version": "one-shot-differential/v1",
        "experiment_id": "experiment",
        "selected_pair": "BTC/EUR",
        "timeframe": "4h",
        "timerange": "20260123-20260718",
        "capital_mode": "one_shot_capital",
        "strategies": [
            {
                "strategy": strategy,
                "status": status,
                "trade_count": 1,
                "checked_fields": ["lifecycle", "final_balances"],
            }
        ],
    }


def test_combine_preserves_failed_diagnostics(tmp_path: Path) -> None:
    paths = []
    for index, strategy in enumerate(STRATEGY_ORDER):
        document = _document(strategy, "failed" if index == 0 else "passed")
        if index == 0:
            document["strategies"][0]["diagnostics"] = {
                "native_trade_count": 25,
                "adapter_trade_count": 19,
            }
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        paths.append(path)

    result = combine(paths)

    assert result["overall_status"] == "failed"
    assert result["strategies"][0]["diagnostics"]["native_trade_count"] == 25


def test_combine_rejects_incomplete_results(tmp_path: Path) -> None:
    path = tmp_path / "one.json"
    path.write_text(json.dumps(_document(STRATEGY_ORDER[0])), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly seven"):
        combine([path])
