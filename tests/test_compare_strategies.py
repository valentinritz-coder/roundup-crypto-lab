import json
from pathlib import Path

import pytest

from roundup_crypto_lab.compare_strategies import REQUIRED_STRATEGIES, create_comparison


def result(name: str) -> dict:
    return {
        "strategy": {
            name: {
                "total_trades": 1,
                "profit_total": 0.1,
                "profit_total_abs": 2,
                "winrate": 1,
                "max_drawdown_account": 0.1,
                "profit_factor": 2,
                "expectancy": 0.1,
            }
        }
    }


def valid_inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {}
    for name in REQUIRED_STRATEGIES:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(result(name)))
        paths[name] = path
    return paths


def test_comparison_emits_required_metrics(tmp_path: Path) -> None:
    rows = create_comparison(valid_inputs(tmp_path))
    assert {row["strategy"] for row in rows} == REQUIRED_STRATEGIES
    assert set(rows[0]) == {
        "strategy",
        "trades",
        "profit_total",
        "profit_total_abs",
        "winrate",
        "max_drawdown_account",
        "profit_factor",
        "expectancy",
    }


def test_comparison_rejects_missing_and_non_numeric_results(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        create_comparison({})
    inputs = valid_inputs(tmp_path)
    document = result("RoundupBreakoutStrategy")
    document["strategy"]["RoundupBreakoutStrategy"]["expectancy"] = "unknown"
    inputs["RoundupBreakoutStrategy"].write_text(json.dumps(document))
    with pytest.raises(ValueError, match="non-numeric expectancy"):
        create_comparison(inputs)
