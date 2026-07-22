"""Compare a native Freqtrade ZIP with one active one-shot artifact."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from roundup_crypto_lab.freqtrade_differential import (
    assert_final_balances_equivalent,
    assert_lifecycle_equivalent,
    normalize_adapter_result_for_native_comparison,
)
from roundup_crypto_lab.freqtrade_reports import _load_backtest


def native(zip_path: Path, strategy: str) -> dict[str, object]:
    data = _load_backtest(zip_path)
    rows = data.get("strategy", {}).get(strategy, {}).get("trades", [])
    if not isinstance(rows, list):
        raise ValueError("native ZIP has no trade ledger")
    trades = []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("native trade invalid")
        trades.append(
            {
                "entry_timestamp": str(r["open_date"]).replace(" ", "T"),
                "exit_timestamp": str(r["close_date"]).replace(" ", "T"),
                "entry_price": r["open_rate"],
                "exit_price": r["close_rate"],
                "entry_gross_stake": r["stake_amount"],
                "quantity": r["amount"],
                "entry_fee": Decimal(str(r["stake_amount"])) * Decimal(str(r["fee_open"])),
                "exit_fee": Decimal(str(r["amount"]))
                * Decimal(str(r["close_rate"]))
                * Decimal(str(r["fee_close"])),
                "exit_reason": "stop_loss"
                if r["exit_reason"] == "trailing_stop_loss"
                else (
                    "exit_signal" if r["exit_reason"] == "close_below_sma20" else r["exit_reason"]
                ),
            }
        )
    metrics = data["strategy"][strategy]
    equity = Decimal(str(metrics["final_balance"]))
    return {
        "trades": trades,
        "free_cash": equity,
        "crypto_value": Decimal("0"),
        "final_equity": equity,
    }


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "combine":
        p = argparse.ArgumentParser()
        p.add_argument("combine")
        p.add_argument("--result", action="append", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        args = p.parse_args()
        args.output.write_text(json.dumps(combine_results(args.result), indent=2) + "\n")
        return
    p = argparse.ArgumentParser()
    p.add_argument("--native-zip", type=Path, required=True)
    p.add_argument("--active", type=Path, required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    active = json.loads(a.active.read_text())
    m = active.get("adapter_metrics", {})
    ledger = active.get("trade_ledger", [])
    if not isinstance(m, dict) or not isinstance(ledger, list):
        raise ValueError("active artifact invalid")
    actual = normalize_adapter_result_for_native_comparison(
        {
            "trades": ledger,
            "free_cash": m["free_cash"],
            "crypto_value": m["crypto_value"],
            "final_equity": m["final_equity"],
        }
    )
    expected = native(a.native_zip, a.strategy)
    assert_lifecycle_equivalent(expected["trades"], actual["trades"])
    assert_final_balances_equivalent(expected, actual)
    e = active["experiment"]
    a.output.write_text(
        json.dumps(
            {
                "schema_version": "one-shot-differential/v1",
                "experiment_id": e["experiment_id"],
                "selected_pair": e["selected_pair"],
                "timerange": e["timerange"],
                "timeframe": e["timeframe"],
                "capital_mode": e["capital_mode"],
                "strategies": [
                    {
                        "strategy": a.strategy,
                        "status": "passed",
                        "trade_count": len(ledger),
                        "checked_fields": ["lifecycle", "final_balances"],
                    }
                ],
            },
            default=str,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()


def combine_results(paths: list[Path]) -> dict[str, object]:
    from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER

    if len(paths) != len(STRATEGY_ORDER):
        raise ValueError("exactly seven differential results are required")
    documents = [json.loads(path.read_text()) for path in paths]
    required = (
        "schema_version",
        "experiment_id",
        "selected_pair",
        "timeframe",
        "timerange",
        "capital_mode",
        "strategies",
    )
    first = documents[0]
    if not isinstance(first, dict) or any(key not in first for key in required):
        raise ValueError("differential result lacks required metadata")
    identity = tuple(first[key] for key in required[:-1])
    strategies = []
    for document in documents:
        if (
            not isinstance(document, dict)
            or tuple(document.get(key) for key in required[:-1]) != identity
        ):
            raise ValueError("differential experiment metadata differs")
        rows = document.get("strategies")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("differential result must contain one strategy")
        row = rows[0]
        if (
            row.get("status") != "passed"
            or not isinstance(row.get("trade_count"), int)
            or row["trade_count"] < 0
        ):
            raise ValueError("differential strategy did not pass")
        if row.get("checked_fields") != ["lifecycle", "final_balances"]:
            raise ValueError("differential checked fields are invalid")
        strategies.append(row)
    if [row.get("strategy") for row in strategies] != list(STRATEGY_ORDER):
        raise ValueError("differential strategies must be complete and ordered")
    return {key: first[key] for key in required[:-1]} | {"strategies": strategies}
