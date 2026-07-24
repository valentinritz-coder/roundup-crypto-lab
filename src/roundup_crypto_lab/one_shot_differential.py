"""Compare native Freqtrade ZIPs with validated active one-shot artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.freqtrade_differential import (
    assert_final_balances_equivalent,
    assert_lifecycle_equivalent,
    normalize_adapter_result_for_native_comparison,
)
from roundup_crypto_lab.freqtrade_reports import _load_backtest

SCHEMA_VERSION = "one-shot-differential/v1"
CHECKED_FIELDS = ["lifecycle", "final_balances"]
_NATIVE_REASON_MAP = {
    "exit_signal": "exit_signal",
    "close_below_sma20": "exit_signal",
    "stop_loss": "stop_loss",
    "trailing_stop_loss": "stop_loss",
    "force_exit": "force_exit",
}


def _decimal(value: object, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be decimal") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _normalize_native_reason(value: object) -> str:
    reason = str(value)
    try:
        return _NATIVE_REASON_MAP[reason]
    except KeyError as error:
        raise ValueError(f"unsupported native exit reason: {reason}") from error


def native(zip_path: Path, strategy: str) -> dict[str, object]:
    """Load the closed-position native representation used by the proven scope."""
    data = _load_backtest(zip_path)
    strategy_map = data.get("strategy")
    if not isinstance(strategy_map, dict) or not isinstance(strategy_map.get(strategy), dict):
        raise ValueError(f"native ZIP has no strategy {strategy}")
    metrics = strategy_map[strategy]
    rows = metrics.get("trades")
    if not isinstance(rows, list):
        raise ValueError("native ZIP has no trade ledger")
    trades: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"native trade {index} is invalid")
        required = (
            "open_date",
            "close_date",
            "open_rate",
            "close_rate",
            "stake_amount",
            "amount",
            "fee_open",
            "fee_close",
            "exit_reason",
        )
        if any(row.get(field) is None for field in required):
            raise ValueError(
                "one-shot differential requires every native position to be closed at timerange end"
            )
        stake = _decimal(row["stake_amount"], "native stake")
        amount = _decimal(row["amount"], "native quantity")
        close_rate = _decimal(row["close_rate"], "native close rate")
        trades.append(
            {
                "entry_timestamp": str(row["open_date"]).replace(" ", "T"),
                "exit_timestamp": str(row["close_date"]).replace(" ", "T"),
                "entry_price": row["open_rate"],
                "exit_price": row["close_rate"],
                "entry_gross_stake": row["stake_amount"],
                "quantity": row["amount"],
                "entry_fee": stake * _decimal(row["fee_open"], "native entry fee ratio"),
                "exit_fee": amount
                * close_rate
                * _decimal(row["fee_close"], "native exit fee ratio"),
                "exit_reason": _normalize_native_reason(row["exit_reason"]),
            }
        )
    if metrics.get("final_balance") is None:
        raise ValueError("native result has no final balance")
    equity = _decimal(metrics["final_balance"], "native final balance")
    return {
        "trades": trades,
        "free_cash": equity,
        "crypto_value": Decimal("0"),
        "final_equity": equity,
    }


def compare_one(native_zip: Path, active_path: Path, strategy: str) -> dict[str, object]:
    """Run one strict differential and return its machine-readable result."""
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if not isinstance(active, dict):
        raise ValueError("active artifact must be an object")
    experiment = active.get("experiment")
    metrics = active.get("adapter_metrics")
    ledger = active.get("trade_ledger")
    if (
        not isinstance(experiment, dict)
        or not isinstance(metrics, dict)
        or not isinstance(ledger, list)
    ):
        raise ValueError("active artifact is invalid")
    if experiment.get("strategy") != strategy:
        raise ValueError("active strategy differs from requested strategy")
    if experiment.get("capital_mode") != "one_shot_capital":
        raise ValueError("native differential only supports one_shot_capital")
    if metrics.get("open_position_state") != "closed" or any(
        isinstance(trade, dict) and trade.get("exit_reason") is None for trade in ledger
    ):
        raise ValueError(
            "one-shot differential requires the active position to be closed at timerange end"
        )
    actual = normalize_adapter_result_for_native_comparison(
        {
            "trades": ledger,
            "free_cash": metrics.get("free_cash"),
            "crypto_value": metrics.get("crypto_value"),
            "final_equity": metrics.get("final_equity"),
        }
    )
    expected = native(native_zip, strategy)
    assert_lifecycle_equivalent(expected["trades"], actual["trades"])
    assert_final_balances_equivalent(expected, actual)
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
                "status": "passed",
                "trade_count": len(ledger),
                "checked_fields": CHECKED_FIELDS,
            }
        ],
    }


def _load_result(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read differential result {path}") from error
    if not isinstance(document, dict):
        raise ValueError("differential result must be an object")
    return document


def combine_results(paths: list[Path]) -> dict[str, object]:
    """Validate and combine exactly one passed result for every strategy."""
    if len(paths) != len(STRATEGY_ORDER):
        raise ValueError("exactly seven differential results are required")
    documents = [_load_result(path) for path in paths]
    metadata_keys = (
        "schema_version",
        "experiment_id",
        "selected_pair",
        "timeframe",
        "timerange",
        "capital_mode",
    )
    first = documents[0]
    if any(first.get(key) in (None, "") for key in metadata_keys):
        raise ValueError("differential result lacks required metadata")
    if first["schema_version"] != SCHEMA_VERSION or first["capital_mode"] != "one_shot_capital":
        raise ValueError("unsupported differential metadata")
    identity = tuple(first[key] for key in metadata_keys)
    strategies: list[dict[str, Any]] = []
    for document in documents:
        if tuple(document.get(key) for key in metadata_keys) != identity:
            raise ValueError("differential experiment metadata differs")
        rows = document.get("strategies")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("differential result must contain one strategy")
        row = dict(rows[0])
        count = row.get("trade_count")
        if (
            row.get("status") != "passed"
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise ValueError("differential strategy did not pass")
        if row.get("checked_fields") != CHECKED_FIELDS:
            raise ValueError("differential checked fields are invalid")
        strategies.append(row)
    names = [row.get("strategy") for row in strategies]
    if names != list(STRATEGY_ORDER):
        raise ValueError("differential strategies must be complete and ordered")
    return {key: first[key] for key in metadata_keys} | {"strategies": strategies}


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=str, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "combine":
        parser = argparse.ArgumentParser(description="Combine one-shot differential results")
        parser.add_argument("command", choices=["combine"])
        parser.add_argument("--result", action="append", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        _write_json(args.output, combine_results(args.result))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-zip", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _write_json(args.output, compare_one(args.native_zip, args.active, args.strategy))


if __name__ == "__main__":
    main()
