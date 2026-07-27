"""Enrich passive benchmark artifacts with shared and DCA-specific metric schemas."""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.cash_flow_metrics import build_cash_flow_metrics
from roundup_crypto_lab.dca_audit import (
    DCA_RESULT_SCHEMA_VERSION,
    validate_decision_ledger,
    write_dca_audit_csvs,
)
from roundup_crypto_lab.passive_benchmarks import parse_timerange

PASSIVE_SCHEMA_VERSION = "passive-benchmarks/v2"


def enrich_passive_result(result: dict[str, Any]) -> dict[str, Any]:
    """Add shared cash-flow metrics while preserving the versioned DCA audit blocks."""
    metadata = result.get("metadata")
    benchmarks = result.get("benchmarks")
    if not isinstance(metadata, dict) or not isinstance(benchmarks, list):
        raise ValueError("passive result must contain metadata and benchmarks")
    _, end = parse_timerange(str(metadata.get("timerange")))
    schedule = metadata.get("contribution_schedule")
    if not isinstance(schedule, list) or not schedule:
        raise ValueError("passive result requires a contribution schedule")
    contributions = [
        {"timestamp": row["contributed_at"], "amount": row["amount"]}
        for row in schedule
        if isinstance(row, dict)
    ]
    if len(contributions) != len(schedule):
        raise ValueError("invalid passive contribution schedule")

    for benchmark in benchmarks:
        if not isinstance(benchmark, dict):
            raise ValueError("passive benchmark row must be an object")
        has_dca_audit = any(
            key in benchmark
            for key in (
                "dca_strategy_result_schema_version",
                "decision_ledger",
                "dca_metrics",
            )
        )
        if has_dca_audit:
            if benchmark.get("dca_strategy_result_schema_version") != DCA_RESULT_SCHEMA_VERSION:
                raise ValueError("passive benchmark has an unsupported DCA result schema")
            validate_decision_ledger(benchmark.get("decision_ledger"))
            if not isinstance(benchmark.get("dca_metrics"), dict):
                raise ValueError("passive benchmark requires DCA-specific metrics")
        curve = benchmark.get("equity_curve")
        if not isinstance(curve, list) or not curve:
            raise ValueError("passive benchmark requires an equity curve")
        snapshots = []
        for row in curve:
            if not isinstance(row, dict):
                continue
            # Passive curves are serialized as floats. Rebuild equity from the
            # serialized components so Decimal auditing does not compare three
            # independently rounded float representations of the same balance.
            cash = Decimal(str(row["cash_balance"]))
            asset_value = Decimal(str(row["crypto_value"]))
            snapshots.append(
                {
                    "timestamp": row["timestamp"],
                    "equity": cash + asset_value,
                    "cash": cash,
                    "asset_value": asset_value,
                    "share_value": row["time_weighted_share_value"],
                }
            )
        if len(snapshots) != len(curve):
            raise ValueError("invalid passive equity row")
        metrics = build_cash_flow_metrics(
            initial_capital=metadata["initial_capital"],
            monthly_budget=metadata["monthly_budget"],
            fee_ratio=metadata["fee"],
            contributions=contributions,
            snapshots=snapshots,
            total_fees=benchmark["fees_paid"],
            period_end=end,
        )
        benchmark["cash_flow_metrics"] = metrics
        historical_profit = Decimal(str(benchmark["profit_total_abs"]))
        common_profit = Decimal(str(metrics["profit_abs"]))
        if abs(historical_profit - common_profit) > Decimal("1e-9"):
            raise ValueError("passive profit differs from cash-flow metrics")
    result["schema_version"] = PASSIVE_SCHEMA_VERSION
    if benchmarks and all(
        row.get("dca_strategy_result_schema_version") == DCA_RESULT_SCHEMA_VERSION
        for row in benchmarks
    ):
        metadata["dca_strategy_result_schema_version"] = DCA_RESULT_SCHEMA_VERSION
    return result


def write_metrics_csv(result: dict[str, Any], output_dir: Path) -> None:
    """Write shared metrics plus exact DCA audit and comparison CSV artifacts."""
    rows = []
    has_dca_audit = all(
        isinstance(benchmark.get("dca_metrics"), dict)
        and isinstance(benchmark.get("decision_ledger"), list)
        for benchmark in result["benchmarks"]
    )
    for benchmark in result["benchmarks"]:
        metrics = benchmark["cash_flow_metrics"]
        row = {
            "category": "passive",
            "method": benchmark["benchmark"],
            "pair": benchmark["pair"],
            "number_of_actions": benchmark["number_of_buys"],
            **metrics,
        }
        if has_dca_audit:
            row.update(
                {
                    f"dca_{key}": value
                    for key, value in benchmark["dca_metrics"].items()
                }
            )
        rows.append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with (output_dir / "cash-flow-metrics.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    if has_dca_audit:
        write_dca_audit_csvs(result, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    enrich_passive_result(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    if args.output_dir:
        write_metrics_csv(result, args.output_dir)


if __name__ == "__main__":
    main()
