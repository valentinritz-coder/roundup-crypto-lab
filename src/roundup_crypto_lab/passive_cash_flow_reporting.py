"""Enrich passive benchmark artifacts with the shared cash-flow metric schema."""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.cash_flow_metrics import build_cash_flow_metrics
from roundup_crypto_lab.passive_benchmarks import parse_timerange

PASSIVE_SCHEMA_VERSION = "passive-benchmarks/v2"


def enrich_passive_result(result: dict[str, Any]) -> dict[str, Any]:
    """Add one audited cash-flow metric block to every passive benchmark row."""
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
        curve = benchmark.get("equity_curve")
        if not isinstance(curve, list) or not curve:
            raise ValueError("passive benchmark requires an equity curve")
        snapshots = [
            {
                "timestamp": row["timestamp"],
                "equity": row["portfolio_value"],
                "cash": row["cash_balance"],
                "asset_value": row["crypto_value"],
                "share_value": row["time_weighted_share_value"],
            }
            for row in curve
            if isinstance(row, dict)
        ]
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
        if Decimal(str(benchmark["profit_total_abs"])) != Decimal(str(metrics["profit_abs"])):
            raise ValueError("passive profit differs from cash-flow metrics")
    result["schema_version"] = PASSIVE_SCHEMA_VERSION
    return result


def write_metrics_csv(result: dict[str, Any], output_dir: Path) -> None:
    """Write one stable, flat cash-flow metric table for downstream comparison."""
    rows = []
    for benchmark in result["benchmarks"]:
        metrics = benchmark["cash_flow_metrics"]
        rows.append(
            {
                "category": "passive",
                "method": benchmark["benchmark"],
                "pair": benchmark["pair"],
                "number_of_actions": benchmark["number_of_buys"],
                **metrics,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with (output_dir / "cash-flow-metrics.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
