import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.one_shot_differential import (
    CHECKED_FIELDS,
    combine_results,
    compare_one,
    native,
)


def _native_zip(path: Path, strategy: str, *, reason: str = "close_below_sma20") -> Path:
    payload = {
        "strategy": {
            strategy: {
                "trades": [
                    {
                        "open_date": "2026-01-01T00:00:00+00:00",
                        "close_date": "2026-01-02T00:00:00+00:00",
                        "open_rate": "100",
                        "close_rate": "110",
                        "stake_amount": "10",
                        "amount": "0.1",
                        "fee_open": "0.01",
                        "fee_close": "0.01",
                        "exit_reason": reason,
                    }
                ],
                "final_balance": "100.89",
            }
        }
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backtest-result.json", json.dumps(payload))
    return path


def _active(path: Path, strategy: str, *, open_position: bool = False) -> Path:
    trade = {
        "entry_timestamp": "2026-01-01T00:00:00+00:00",
        "exit_timestamp": "2026-01-02T00:00:00+00:00",
        "entry_price": "100",
        "exit_price": "110",
        "entry_gross_stake": "10.0000000001",
        "quantity": "0.100000001",
        "entry_fee": "0.1000000001",
        "exit_fee": "0.1100000011",
        "exit_reason": "exit_signal",
    }
    if open_position:
        trade.update(
            {
                "exit_timestamp": None,
                "exit_price": None,
                "exit_fee": None,
                "exit_reason": None,
            }
        )
    payload = {
        "experiment": {
            "strategy": strategy,
            "experiment_id": "experiment",
            "selected_pair": "BTC/EUR",
            "timeframe": "4h",
            "timerange": "20260101-20260103",
            "capital_mode": "one_shot_capital",
        },
        "adapter_metrics": {
            "free_cash": "100.89",
            "crypto_value": "0",
            "final_equity": "100.89",
            "open_position_state": "open_marked_at_final_close" if open_position else "closed",
        },
        "trade_ledger": [trade],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_compare_one_reads_zip_normalizes_rounding_and_writes_passed_result(tmp_path) -> None:
    strategy = STRATEGY_ORDER[0]
    result = compare_one(
        _native_zip(tmp_path / "native.zip", strategy),
        _active(tmp_path / "active.json", strategy),
        strategy,
    )
    assert result["strategies"] == [
        {
            "strategy": strategy,
            "status": "passed",
            "trade_count": 1,
            "checked_fields": CHECKED_FIELDS,
        }
    ]


def test_native_reason_normalization_and_unknown_reason_rejection(tmp_path) -> None:
    strategy = STRATEGY_ORDER[0]
    normalized = native(
        _native_zip(tmp_path / "stop.zip", strategy, reason="trailing_stop_loss"),
        strategy,
    )
    assert normalized["trades"][0]["exit_reason"] == "stop_loss"
    with pytest.raises(ValueError, match="unsupported native exit reason"):
        native(_native_zip(tmp_path / "bad.zip", strategy, reason="force_exit"), strategy)


def test_open_positions_are_explicitly_outside_the_differential_scope(tmp_path) -> None:
    strategy = STRATEGY_ORDER[0]
    with pytest.raises(ValueError, match="requires the active position to be closed"):
        compare_one(
            _native_zip(tmp_path / "native.zip", strategy),
            _active(tmp_path / "active.json", strategy, open_position=True),
            strategy,
        )


def _individual(path: Path, strategy: str, *, experiment_id: str = "experiment") -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "one-shot-differential/v1",
                "experiment_id": experiment_id,
                "selected_pair": "BTC/EUR",
                "timeframe": "4h",
                "timerange": "20260101-20260103",
                "capital_mode": "one_shot_capital",
                "strategies": [
                    {
                        "strategy": strategy,
                        "status": "passed",
                        "trade_count": 1,
                        "checked_fields": CHECKED_FIELDS,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_combine_requires_one_identical_passed_result_per_strategy(tmp_path) -> None:
    paths = [
        _individual(tmp_path / f"{index}.json", strategy)
        for index, strategy in enumerate(STRATEGY_ORDER)
    ]
    combined = combine_results(paths)
    assert [row["strategy"] for row in combined["strategies"]] == list(STRATEGY_ORDER)
    broken = list(paths)
    broken[-1] = _individual(tmp_path / "broken.json", STRATEGY_ORDER[-1], experiment_id="other")
    with pytest.raises(ValueError, match="metadata differs"):
        combine_results(broken)


def test_module_combine_cli_executes_after_all_functions_are_defined(tmp_path) -> None:
    paths = [
        _individual(tmp_path / f"{index}.json", strategy)
        for index, strategy in enumerate(STRATEGY_ORDER)
    ]
    output = tmp_path / "combined.json"
    command = [sys.executable, "-m", "roundup_crypto_lab.one_shot_differential", "combine"]
    for path in paths:
        command.extend(["--result", str(path)])
    command.extend(["--output", str(output)])
    env = os.environ.copy()
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["experiment_id"] == "experiment"
