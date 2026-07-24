"""Capture strict one-shot differential failures without losing artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
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

MONEY_WARNING_TOLERANCE = Decimal("0.01")
PRICE_WARNING_TOLERANCE = Decimal("0.10")
QUANTITY_WARNING_TOLERANCE = Decimal("0.00000001")
STOP_PRICE_RELATIVE_WARNING = Decimal("0.01")
STOP_BALANCE_WARNING_TOLERANCE = Decimal("1.00")


def _load_active(path: Path, strategy: str) -> tuple[dict[str, Any], dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    experiment = document.get("experiment")
    metrics = document.get("adapter_metrics")
    ledger = document.get("trade_ledger")
    if (
        not isinstance(experiment, dict)
        or not isinstance(metrics, dict)
        or not isinstance(ledger, list)
    ):
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


def _decimal(value: object, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be decimal") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _first_trade_difference(
    native_trades: list[dict[str, object]],
    adapter_trades: list[dict[str, object]],
) -> dict[str, object]:
    common = min(len(native_trades), len(adapter_trades))
    index = next(
        (i for i in range(common) if native_trades[i] != adapter_trades[i]),
        common,
    )
    return {
        "first_divergent_index": index,
        "native_trade_count": len(native_trades),
        "adapter_trade_count": len(adapter_trades),
        "native_trade": native_trades[index] if index < len(native_trades) else None,
        "adapter_trade": adapter_trades[index] if index < len(adapter_trades) else None,
        "native_remaining": native_trades[index : index + 5],
        "adapter_remaining": adapter_trades[index : index + 5],
    }


def _economic_warnings(
    expected: dict[str, object], actual: dict[str, object]
) -> list[dict[str, object]] | None:
    native_trades = expected["trades"]
    adapter_trades = actual["trades"]
    if not isinstance(native_trades, list) or not isinstance(adapter_trades, list):
        return None
    if len(native_trades) != len(adapter_trades):
        return None

    warnings: list[dict[str, object]] = []
    stop_model_warning = False
    exact_fields = ("entry_timestamp", "exit_timestamp", "exit_reason")
    numeric_tolerances = {
        "entry_price": PRICE_WARNING_TOLERANCE,
        "exit_price": PRICE_WARNING_TOLERANCE,
        "entry_gross_stake": MONEY_WARNING_TOLERANCE,
        "quantity": QUANTITY_WARNING_TOLERANCE,
        "entry_fee": MONEY_WARNING_TOLERANCE,
        "exit_fee": MONEY_WARNING_TOLERANCE,
    }

    for index, (native_trade, adapter_trade) in enumerate(
        zip(native_trades, adapter_trades, strict=True)
    ):
        if not isinstance(native_trade, dict) or not isinstance(adapter_trade, dict):
            return None
        for field in exact_fields:
            if native_trade.get(field) != adapter_trade.get(field):
                return None
        for field, tolerance in numeric_tolerances.items():
            native_value = _decimal(native_trade.get(field), f"native trade {field}")
            adapter_value = _decimal(adapter_trade.get(field), f"adapter trade {field}")
            delta = abs(native_value - adapter_value)
            if delta <= tolerance:
                if delta:
                    warnings.append(
                        {
                            "kind": "rounding",
                            "trade_index": index,
                            "field": field,
                            "native": native_value,
                            "adapter": adapter_value,
                            "absolute_delta": delta,
                            "tolerance": tolerance,
                        }
                    )
                continue
            if (
                field == "exit_price"
                and native_trade.get("exit_reason") == "stop_loss"
                and native_value > 0
                and delta / native_value <= STOP_PRICE_RELATIVE_WARNING
            ):
                stop_model_warning = True
                warnings.append(
                    {
                        "kind": "supported_stop_model_difference",
                        "trade_index": index,
                        "field": field,
                        "native": native_value,
                        "adapter": adapter_value,
                        "absolute_delta": delta,
                        "relative_delta": delta / native_value,
                        "tolerance": STOP_PRICE_RELATIVE_WARNING,
                    }
                )
                continue
            return None

    balance_tolerance = (
        STOP_BALANCE_WARNING_TOLERANCE if stop_model_warning else MONEY_WARNING_TOLERANCE
    )
    for field in ("free_cash", "crypto_value", "final_equity"):
        native_value = _decimal(expected.get(field), f"native {field}")
        adapter_value = _decimal(actual.get(field), f"adapter {field}")
        delta = abs(native_value - adapter_value)
        if delta > balance_tolerance:
            return None
        if delta:
            warnings.append(
                {
                    "kind": "balance_rounding" if not stop_model_warning else "stop_model_impact",
                    "field": field,
                    "native": native_value,
                    "adapter": adapter_value,
                    "absolute_delta": delta,
                    "tolerance": balance_tolerance,
                }
            )
    return warnings


def diagnose(native_zip: Path, active_path: Path, strategy: str) -> dict[str, object]:
    try:
        return compare_one(native_zip, active_path, strategy)
    except (AssertionError, ValueError, KeyError, TypeError) as error:
        experiment, actual = _load_active(active_path, strategy)
        expected = native(native_zip, strategy)
        warnings = _economic_warnings(expected, actual)
        status = "passed_with_warnings" if warnings is not None else "failed"
        row: dict[str, object] = {
            "strategy": strategy,
            "status": status,
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
        if warnings is not None:
            row["warnings"] = warnings
        return {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": experiment.get("experiment_id"),
            "selected_pair": experiment.get("selected_pair"),
            "timeframe": experiment.get("timeframe"),
            "timerange": experiment.get("timerange"),
            "capital_mode": experiment.get("capital_mode"),
            "strategies": [row],
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
    metadata = (
        "schema_version",
        "experiment_id",
        "selected_pair",
        "timeframe",
        "timerange",
        "capital_mode",
    )
    first = documents[0]
    identity = tuple(first.get(key) for key in metadata)
    if any(value in (None, "") for value in identity):
        raise ValueError("diagnostic metadata is incomplete")
    rows: list[dict[str, Any]] = []
    for document in documents:
        if tuple(document.get(key) for key in metadata) != identity:
            raise ValueError("diagnostic experiment metadata differs")
        strategies = document.get("strategies")
        if (
            not isinstance(strategies, list)
            or len(strategies) != 1
            or not isinstance(strategies[0], dict)
        ):
            raise ValueError("diagnostic result must contain one strategy")
        rows.append(dict(strategies[0]))
    if [row.get("strategy") for row in rows] != list(STRATEGY_ORDER):
        raise ValueError("diagnostic strategies must be complete and ordered")
    statuses = {row.get("status") for row in rows}
    if "failed" in statuses:
        overall_status = "failed"
    elif "passed_with_warnings" in statuses:
        overall_status = "passed_with_warnings"
    else:
        overall_status = "passed"
    return {key: first[key] for key in metadata} | {
        "overall_status": overall_status,
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
        raise SystemExit(1 if result["overall_status"] == "failed" else 0)
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-zip", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _write(args.output, diagnose(args.native_zip, args.active, args.strategy))


if __name__ == "__main__":
    main()
