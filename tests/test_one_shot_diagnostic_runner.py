import json
import subprocess
import sys
from pathlib import Path

import pytest

from roundup_crypto_lab.active_cross_validation import validate_differential
from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.one_shot_diagnostic_runner import register_native_exit_reasons
from roundup_crypto_lab.one_shot_differential import _normalize_native_reason


def _document(strategy: str, status: str = "passed") -> dict[str, object]:
    row: dict[str, object] = {
        "strategy": strategy,
        "status": status,
        "trade_count": 1,
        "checked_fields": ["lifecycle", "final_balances"],
    }
    if status == "failed":
        row.update(
            {
                "error": "fixture divergence",
                "diagnostics": {
                    "native_trade_count": 1,
                    "adapter_trade_count": 1,
                },
            }
        )
    return {
        "schema_version": "one-shot-differential/v1",
        "experiment_id": "experiment",
        "selected_pair": "BTC/EUR",
        "timeframe": "4h",
        "timerange": "20260101-20260103",
        "capital_mode": "one_shot_capital",
        "strategies": [row],
    }


def test_research_exit_tags_normalize_to_exit_signal() -> None:
    register_native_exit_reasons()
    for reason in (
        "control_breakdown_10",
        "momentum_lost_or_below_sma20",
        "rsi2_or_sma20_mean_reversion",
        "distance_reverted_to_ema20",
        "obv_or_sma20_breakdown",
        "trend_quality_lost_or_below_sma20",
        "donchian_retest_failed_or_below_sma20",
        "capitulation_recovery_completed",
    ):
        assert _normalize_native_reason(reason) == "exit_signal"


def test_runner_combine_preserves_failed_rows_but_exits_successfully(tmp_path: Path) -> None:
    paths = []
    for index, strategy in enumerate(STRATEGY_ORDER):
        path = tmp_path / f"{index}.json"
        path.write_text(
            json.dumps(_document(strategy, "failed" if index == 0 else "passed")),
            encoding="utf-8",
        )
        paths.append(path)

    output = tmp_path / "combined.json"
    command = [
        sys.executable,
        "-m",
        "roundup_crypto_lab.one_shot_diagnostic_runner",
        "combine",
    ]
    for path in paths:
        command.extend(["--result", str(path)])
    command.extend(["--output", str(output)])

    completed = subprocess.run(command, text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["overall_status"] == "failed"
    assert len(result["strategies"]) == len(STRATEGY_ORDER)


def test_cross_validation_accepts_explicit_failed_diagnostics() -> None:
    experiment = {
        "experiment_id": "experiment",
        "selected_pair": "BTC/EUR",
        "timeframe": "4h",
        "timerange": "20260101-20260103",
        "capital_mode": "one_shot_capital",
    }
    rows = [
        _document(strategy, "failed" if index == 0 else "passed")["strategies"][0]
        for index, strategy in enumerate(STRATEGY_ORDER)
    ]
    differential = {
        "schema_version": "one-shot-differential/v1",
        **experiment,
        "overall_status": "failed",
        "strategies": rows,
    }

    validated = validate_differential(differential, experiment)

    assert validated[0]["status"] == "failed"


def test_cross_validation_rejects_failed_row_without_diagnostics() -> None:
    experiment = {
        "experiment_id": "experiment",
        "selected_pair": "BTC/EUR",
        "timeframe": "4h",
        "timerange": "20260101-20260103",
        "capital_mode": "one_shot_capital",
    }
    rows = [
        _document(strategy, "failed" if index == 0 else "passed")["strategies"][0]
        for index, strategy in enumerate(STRATEGY_ORDER)
    ]
    rows[0].pop("diagnostics")
    differential = {
        "schema_version": "one-shot-differential/v1",
        **experiment,
        "overall_status": "failed",
        "strategies": rows,
    }

    with pytest.raises(ValueError, match="failed differential must include diagnostics"):
        validate_differential(differential, experiment)
