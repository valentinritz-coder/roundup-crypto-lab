"""Deterministic, long-only passive benchmarks on prepared Kraken OHLCV data.

Purchases use the open of the first candle at or after their scheduled UTC instant.
There is no sale: final values use the last eligible candle close and include no sale fee.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.dca_baselines import (
    DEFAULT_STRATEGY_REGISTRY,
    baseline_name,
    deploy_registered_baseline,
    deployment_method,
    registered_baselines,
    strategy_metadata,
)
from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.deployment_engine import (
    INTERVAL,
    PURCHASE_LEDGER_FIELDS,
    TIMEFRAME,
    WEEKDAYS,
    DeploymentBucket,
    build_result,
    candle_metadata,
    deployment_buckets,
    load_kraken_candles,
    number,
    parse_timerange,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule

__all__ = [
    "DeploymentBucket",
    "INTERVAL",
    "TIMEFRAME",
    "WEEKDAYS",
    "buy_and_hold",
    "dca",
    "deployment_buckets",
    "load_kraken_candles",
    "parse_timerange",
    "run_passive_benchmarks",
    "write_details",
]


def buy_and_hold(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Removed legacy API; use ``run_passive_benchmarks`` with ``InvestmentPlan`` inputs."""
    raise ValueError("buy_and_hold is removed; use the shared InvestmentPlan benchmark runner")


def dca(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Removed legacy API that accepted an independent contribution amount."""
    raise ValueError("dca is removed; use --monthly-budget with the shared InvestmentPlan")


def run_passive_benchmarks(
    data_dir: Path,
    pairs: list[str],
    timeframe: str,
    timerange: str,
    initial_capital: Decimal | str = Decimal("200"),
    monthly_budget: Decimal | str = Decimal("40"),
    fee: Decimal | str = Decimal("0.004"),
    contribution_day: int = 23,
    weekly_day: str = "monday",
    registry_path: Path = DEFAULT_STRATEGY_REGISTRY,
) -> dict[str, Any]:
    """Run identically funded registered passive deployment strategies."""
    start, end = parse_timerange(timerange)
    plan = InvestmentPlan(initial_capital, monthly_budget, fee, contribution_day)
    if weekly_day.lower() not in WEEKDAYS:
        raise ValueError(f"weekly day must be one of: {', '.join(WEEKDAYS)}")
    if not pairs:
        raise ValueError("at least one pair is required")
    registry = load_registry(registry_path)
    definitions = registered_baselines(
        registry,
        include_immediate=True,
        include_monthly=False,
    )
    events = contribution_schedule(plan, start, end)
    schedule_metadata = [
        {
            "contributed_at": event.contributed_at.isoformat(),
            "amount": number(event.amount),
            "kind": event.kind,
        }
        for event in events
    ]
    benchmarks, pair_metadata = [], {}
    for pair in pairs:
        candles = load_kraken_candles(data_dir, pair, timeframe, timerange)
        pair_metadata[pair] = candle_metadata(candles, timerange)
        for definition in definitions:
            overrides = (
                {"weekday": WEEKDAYS[weekly_day.lower()]}
                if definition.implementation == "fixed_weekly"
                else None
            )
            purchases = deploy_registered_baseline(
                plan,
                events,
                candles,
                definition,
                parameter_overrides=overrides,
            )
            result = build_result(
                baseline_name(definition),
                pair,
                candles,
                events,
                purchases,
            )
            result["deployment_method"] = deployment_method(definition)
            result["strategy"] = strategy_metadata(
                registry,
                definition,
                parameter_overrides=overrides,
            )
            result["contribution_schedule"] = schedule_metadata
            benchmarks.append(result)
    total = sum((event.amount for event in events), Decimal("0"))
    return {
        "metadata": {
            "timerange": timerange,
            "timeframe": timeframe,
            "fee": number(plan.fee_ratio),
            "data_dir": str(data_dir),
            "pairs": pairs,
            "initial_capital": number(plan.initial_capital),
            "monthly_budget": number(plan.monthly_budget),
            "contribution_day": plan.contribution_day,
            "contribution_schedule": schedule_metadata,
            "total_contributions": number(total),
            "pair_candle_coverage": pair_metadata,
            "strategy_registry": {
                "registry_schema_version": registry.registry_schema_version,
                "registry_id": registry.registry_id,
                "registry_digest": registry.digest,
            },
        },
        "benchmarks": benchmarks,
    }


def write_details(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for benchmark in result["benchmarks"]:
        stem = (
            benchmark["benchmark"].replace("And", "-and-").replace("DCA", "-dca").lower()
            + "-"
            + benchmark["pair"].replace("/", "-").lower()
        )
        for suffix, rows in (
            ("equity", benchmark["equity_curve"]),
            ("purchase-ledger", benchmark["purchase_ledger"]),
        ):
            fieldnames = list(rows[0]) if rows else PURCHASE_LEDGER_FIELDS
            with (output_dir / f"{stem}-{suffix}.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/kraken"))
    parser.add_argument("--pairs", nargs="+", default=["BTC/EUR", "ETH/EUR"])
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--timerange", required=True)
    parser.add_argument("--initial-capital", default="200")
    parser.add_argument("--fee", default="0.004")
    parser.add_argument("--monthly-budget", default="40")
    parser.add_argument("--contribution-day", type=int, default=23)
    parser.add_argument("--weekly-day", default="monday")
    parser.add_argument(
        "--strategy-registry",
        type=Path,
        default=DEFAULT_STRATEGY_REGISTRY,
    )
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    legacy_options = ("--daily-contribution", "--weekly-contribution")
    if any(
        argument == option or argument.startswith(f"{option}=")
        for argument in sys.argv[1:]
        for option in legacy_options
    ):
        parser.error(
            "--daily-contribution and --weekly-contribution were removed; use --monthly-budget"
        )
    args = parser.parse_args()
    result = run_passive_benchmarks(
        args.data_dir,
        args.pairs,
        args.timeframe,
        args.timerange,
        args.initial_capital,
        args.monthly_budget,
        args.fee,
        args.contribution_day,
        args.weekly_day,
        args.strategy_registry,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    if args.output_dir:
        write_details(result, args.output_dir)


if __name__ == "__main__":
    main()
