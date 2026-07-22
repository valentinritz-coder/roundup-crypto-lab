import json
from pathlib import Path

import pytest

from roundup_crypto_lab.breakout_comparison import (
    parse_timerange,
    summary_markdown,
    validate_comparison,
    validate_metadata,
)
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
        "category",
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


def passive_benchmark(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "benchmark": "DailyDCA",
        "pair": "BTC/EUR",
        "number_of_buys": 2,
        "profit_total": 0.1,
        "profit_total_abs": 2.0,
        "max_drawdown_time_weighted": 0.2,
    }
    result.update(overrides)
    return result


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"profit_total": float("nan")}, "profit_total"),
        ({"profit_total_abs": float("inf")}, "profit_total_abs"),
        ({"number_of_buys": True}, "number_of_buys"),
        ({"benchmark": ""}, "name"),
        ({"pair": 4}, "pair"),
        ({"max_drawdown_time_weighted": 1.1}, "drawdown"),
    ],
)
def test_comparison_rejects_invalid_passive_benchmark_fields(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    path = tmp_path / "benchmarks.json"
    path.write_text(json.dumps({"benchmarks": [passive_benchmark(**overrides)]}))
    with pytest.raises(ValueError, match=message):
        create_comparison(valid_inputs(tmp_path), benchmark_path=path)


def test_comparison_rejects_duplicate_passive_benchmark_pair(tmp_path: Path) -> None:
    path = tmp_path / "benchmarks.json"
    row = passive_benchmark()
    path.write_text(json.dumps({"benchmarks": [row, row]}))
    with pytest.raises(ValueError, match="duplicate"):
        create_comparison(valid_inputs(tmp_path), benchmark_path=path)


def test_timerange_validation_is_strict() -> None:
    assert parse_timerange("20260123-20260722")
    for timerange in (
        "20260123",
        "2026-01-23-20260722",
        "20260230-20260722",
        "20260722-20260123",
        "20260123-20260123",
        "$(whoami)-20260722",
    ):
        with pytest.raises(ValueError):
            parse_timerange(timerange)


def test_comparison_validation_and_summary_keep_raw_values(tmp_path: Path) -> None:
    output = tmp_path / "comparison.json"
    rows = create_comparison(valid_inputs(tmp_path))
    output.write_text(json.dumps(rows))
    validated = validate_comparison(output)
    summary = summary_markdown(
        validated,
        {
            "timerange": "20260123-20260722",
            "timeframe": "4h",
            "freqtrade_version": "2026.6",
            "commit_sha": "abc",
            "run_date_utc": "2026-07-22T00:00:00Z",
        },
    )
    assert "10.00%" in summary
    assert "Raw `profit_total`, `winrate`, and `max_drawdown_account`" in summary
    assert json.loads(output.read_text())[0]["profit_total"] == 0.1
    validate_metadata(
        {
            "timerange": "20260123-20260722",
            "timeframe": "4h",
            "commit_sha": "abc",
            "python_version": "Python 3.12",
            "freqtrade_version": "2026.6",
            "run_date_utc": "2026-07-22T00:00:00Z",
            "strategies": [
                "RoundupBreakoutStrategy",
                "RoundupBreakoutTrendStrategy",
                "RoundupBreakoutAtrStrategy",
                "RoundupBreakoutAtrVolumeStrategy",
            ],
        }
    )
