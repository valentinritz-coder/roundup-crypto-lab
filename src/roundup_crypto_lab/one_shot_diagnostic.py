"""Capture strict one-shot differential failures without losing artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.freqtrade_differential import normalize_adapter_result_for_native_comparison
from roundup_crypto_lab.one_shot_differential import (
    CHECKED_FIELDS,
    SCHEMA_VERSION,
    compare_one,
    native,
)


def _load_active(path: Path, strategy: str) -> tuple[dict[str, Any], dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    experiment = document.get("experiment")
    metrics = document.get("adapter_metrics")
    ledger = document.get("trade_ledger")
    if not isinstance(experiment, dict) or not isinstance(metrics, dict) or not isinstance(ledger, list):
        raise ValueError("active artifact is invalid")
    if experiment.get("strategy") != strategy:
        raise ValueError("active strategy differs from requested strategy")
    normalized = normalize_adapter_result_for_native_comparison(
        {
            "trades": ledger,
            "free_cash": metrics.get("free_cash"),
            "crypto_value": metrics.get("crypto_value"),
            "final_equity": metrics.get("final_equity"),
        }
    )
    return experiment, normalized


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _first_trade_difference(native_trades: list[dict[str, object]], adapter_trades: list[dict[str, object]]) -> dict[str, object]:
    common = min(len(native_trades), len(adapter_trades))
    index = next((i for i in range(common) if native_trades[i] != adapter_trades[i]), common)
    return {
        "first_divergent_index": index,
        "native_trade_count": len(native_trades),
        "adapter_trade_count": len(adapter_trades),
        "native_trade": native_trades[index] if index < len(native_trades) else None,
        "adapter_trade": adapter_trades[index] if index < len(adapter_trades) else None,
        "native_remaining": native_trades[index : index + 5],
        "adapter_remaining": adapter_trades[index : index + 5],
    }


def diagnose(native_zip: Path, active_path: Path, strategy: str) -> dict[str, object]:
    try:
        return compare_one(native_zip, active_path, strategy)
    except (AssertionError, ValueError, KeyError, TypeError) as error:
        experiment, actual = _load_active(active_path, strategy)
        expected = native(native_zip, strategy)
        return {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": experiment.get("experiment_id"),
            "selected_pair": experiment.get("selected_pair"),
            "timeframe": experiment.get("timeframe"),
            "timerange": experiment.get("timerange"),
            "capital_mode": experiment.get("capital_mode"),
            "strategies": [
                {
                    "strategy": strategy,
                    "status": "failed",
                    "trade_count": len(actual["trades"]),
                    "checked_fields": CHECKED_FIELDS,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "diagnostics": _first_trade_difference(expected["trades"], actual["trades"]),
                    "native_balances": {
                        "free_cash": expected["free_cash"],
                        "crypto_value": expected["crypto_value"],
                        "final_equity": expected["final_equity"],
                    },
                    "adapter_balances": {
                        "free_cash": actual["free_cash"],
                        "crypto_value": actual["crypto_value"],
                        "final_equity": actual["final_equity"],
                    },
                }
            ],
        }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("diagnostic result must be an object")
    return value


def combine(paths: list[Path]) -> dict[str, object]:
    if len(paths) != len(STRATEGY_ORDER):
        raise ValueError("exactly seven diagnostic results are required")
    documents = [_load(path) for path in paths]
    metadata = ("schema_version", "experiment_id", "selected_pair", "timeframe", "timerange", "capital_mode")
    first = documents[0]
    identity = tuple(first.get(key) for key in metadata)
    if any(value in (None, "") for value in identity):
        raise ValueError("diagnostic metadata is incomplete")
    rows: list[dict[str, Any]] = []
    for document in documents:
        if tuple(document.get(key) for key in metadata) != identity:
            raise ValueError("diagnostic experiment metadata differs")
        strategies = document.get("strategies")
        if not isinstance(strategies, list) or len(strategies) != 1 or not isinstance(strategies[0], dict):
            raise ValueError("diagnostic result must contain one strategy")
        rows.append(dict(strategies[0]))
    if [row.get("strategy") for row in rows] != list(STRATEGY_ORDER):
        raise ValueError("diagnostic strategies must be complete and ordered")
    return {key: first[key] for key in metadata} | {
        "overall_status": "passed" if all(row.get("status") == "passed" for row in rows) else "failed",
        "strategies": rows,
    }


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "combine":
        parser = argparse.ArgumentParser()
        parser.add_argument("command", choices=["combine"])
        parser.add_argument("--result", action="append", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        result = combine(args.result)
        _write(args.output, result)
        raise SystemExit(0 if result["overall_status"] == "passed" else 1)
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-zip", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _write(args.output, diagnose(args.native_zip, args.active, args.strategy))


if __name__ == "__main__":
    main()
