"""Run one passive DCA frequency scenario under a versioned cost profile."""

from __future__ import annotations

import argparse
import csv
import json
import platform
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.dca_baselines import DEFAULT_STRATEGY_REGISTRY
from roundup_crypto_lab.dca_costed_execution import (
    registered_frequency_strategies,
    run_costed_strategy,
)
from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.deployment_engine import (
    WEEKDAYS,
    load_kraken_candles,
    parse_timerange,
)
from roundup_crypto_lab.execution_costs import (
    DEFAULT_COST_PROFILE_DIR,
    resolve_cost_profile,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule

SCHEMA_VERSION = "dca-cost-profile-scenario/v1"
MANIFEST_VERSION = "dca-cost-profile-scenario-manifest/v1"


def _canonical(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _comparison_row(result: Mapping[str, Any]) -> dict[str, Any]:
    costs = result["execution_costs"]
    return {
        "method": result["benchmark"],
        "strategy_id": result["strategy"]["strategy_id"],
        "final_value": result["final_value_exact"],
        "final_crypto_quantity": result["quantity_exact"],
        "final_uninvested_cash": result["cash_balance_exact"],
        "order_count": costs["order_count"],
        "average_order_size": costs["average_order_size"],
        "trading_fees_paid": costs["trading_fees_paid"],
        "fixed_order_fees_paid": costs["fixed_order_fees_paid"],
        "explicit_fees_paid": costs["explicit_fees_paid"],
        "estimated_spread_cost": costs["estimated_spread_cost"],
        "total_execution_cost": costs["total_execution_cost"],
    }


def run_cost_profile_scenario(
    *,
    data_dir: Path,
    pair: str,
    timeframe: str,
    timerange: str,
    registry_path: Path,
    initial_capital: str,
    monthly_budget: str,
    contribution_day: int,
    weekly_day: str,
    repository_commit: str,
    cost_profile_reference: Path | str | None = None,
    legacy_fee: str | None = None,
    cost_profile_dir: Path = DEFAULT_COST_PROFILE_DIR,
) -> dict[str, Any]:
    if weekly_day.lower() not in WEEKDAYS:
        raise ValueError(f"weekly day must be one of: {', '.join(WEEKDAYS)}")
    if not repository_commit.strip():
        raise ValueError("repository commit must be non-empty")
    profile = resolve_cost_profile(
        cost_profile_reference,
        legacy_fee_ratio=legacy_fee,
        search_dir=cost_profile_dir,
    )
    start, end = parse_timerange(timerange)
    plan = InvestmentPlan(
        initial_capital,
        monthly_budget,
        profile.trading_fee_ratio,
        contribution_day,
    )
    events = contribution_schedule(plan, start, end)
    candles = load_kraken_candles(
        data_dir,
        pair,
        timeframe,
        timerange,
    )
    registry = load_registry(registry_path)
    definitions = registered_frequency_strategies(registry)
    results = [
        run_costed_strategy(
            plan=plan,
            events=events,
            candles=candles,
            pair=pair,
            definition=definition,
            profile=profile,
            weekly_day=WEEKDAYS[weekly_day.lower()],
        )
        for definition in definitions
    ]
    total_contributions = sum((event.amount for event in events), Decimal("0"))
    for result in results:
        if Decimal(str(result["total_contributions"])) != total_contributions:
            raise ValueError("frequency strategies received incompatible contributions")
        if result["execution_costs"]["cost_profile"]["profile_digest"] != profile.digest:
            raise ValueError("frequency result contains an incompatible cost profile")

    return {
        "schema_version": SCHEMA_VERSION,
        "scenario": {
            "pair": pair,
            "timeframe": timeframe,
            "timerange": timerange,
            "initial_capital": initial_capital,
            "monthly_budget": monthly_budget,
            "contribution_day": contribution_day,
            "weekly_day": weekly_day.lower(),
            "total_contributions": _canonical(total_contributions),
            "repository_commit": repository_commit,
        },
        "registry": {
            "registry_id": registry.registry_id,
            "registry_digest": registry.digest,
        },
        "execution_cost_profile": profile.artifact(),
        "results": results,
        "comparison": [_comparison_row(result) for result in results],
    }


def write_cost_profile_outputs(
    payload: Mapping[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cost-profile-scenario.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )
    rows = payload["comparison"]
    with (output_dir / "cost-profile-comparison.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]) if rows else ["method"],
        )
        writer.writeheader()
        writer.writerows(rows)

    (output_dir / "resolved-cost-profile.json").write_text(
        json.dumps(
            payload["execution_cost_profile"],
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "repository_commit": payload["scenario"]["repository_commit"],
        "registry_digest": payload["registry"]["registry_digest"],
        "cost_profile_digest": payload["execution_cost_profile"]["profile_digest"],
        "scenario": payload["scenario"],
        "python_version": platform.python_version(),
        "pandas_version": pd.__version__,
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    (output_dir / "reproducibility-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Passive DCA execution-cost comparison",
        "",
        f"Pair: `{payload['scenario']['pair']}`",
        (
            "Cost profile: "
            f"`{payload['execution_cost_profile']['cost_profile_id']}@"
            f"{payload['execution_cost_profile']['profile_version']}`"
        ),
        "",
        "| Method | Final value | Quantity | Explicit fees | Spread cost | Orders |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['method']} | {row['final_value']} | "
            f"{row['final_crypto_quantity']} | {row['explicit_fees_paid']} | "
            f"{row['estimated_spread_cost']} | {row['order_count']} |"
        )
    lines.extend(
        [
            "",
            "Fixed-order fees appear only in profiles explicitly labelled as sensitivity analyses.",
            "",
        ]
    )
    (output_dir / "job-summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("user_data/data/kraken"),
    )
    parser.add_argument("--pair", required=True, choices=("BTC/EUR", "ETH/EUR"))
    parser.add_argument("--timeframe", required=True, choices=("4h",))
    parser.add_argument("--timerange", required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_STRATEGY_REGISTRY,
    )
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--weekly-day", default="monday")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--cost-profile")
    inputs.add_argument("--fee")
    parser.add_argument(
        "--cost-profile-dir",
        type=Path,
        default=DEFAULT_COST_PROFILE_DIR,
    )
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    payload = run_cost_profile_scenario(
        data_dir=args.data_dir,
        pair=args.pair,
        timeframe=args.timeframe,
        timerange=args.timerange,
        registry_path=args.registry,
        initial_capital=args.initial_capital,
        monthly_budget=args.monthly_budget,
        contribution_day=args.contribution_day,
        weekly_day=args.weekly_day,
        repository_commit=args.repository_commit,
        cost_profile_reference=args.cost_profile,
        legacy_fee=args.fee,
        cost_profile_dir=args.cost_profile_dir,
    )
    write_cost_profile_outputs(payload, args.output_dir)


if __name__ == "__main__":
    main()
