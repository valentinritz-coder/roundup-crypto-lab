"""Run passive strategies under the same one-shot or recurring scenario as active results."""

from __future__ import annotations

import argparse
import json
from calendar import monthrange
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.dca_baselines import (
    DEFAULT_STRATEGY_REGISTRY,
    baseline_name,
    deploy_legacy_baseline,
    deploy_registered_baseline,
    deployment_method,
    registered_baselines,
    strategy_metadata,
)
from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.deployment_engine import (
    WEEKDAYS,
    build_result,
    candle_metadata,
    load_kraken_candles,
    number,
    parse_timerange,
)
from roundup_crypto_lab.investment_plan import (
    CashFlowEvent,
    InvestmentPlan,
    contribution_schedule,
)
from roundup_crypto_lab.passive_cash_flow_reporting import (
    enrich_passive_result,
    write_metrics_csv,
)

CAPITAL_MODES = frozenset({"one_shot_capital", "recurring_monthly_contributions"})


def _next_month(value: datetime) -> datetime:
    """Compatibility helper retained for callers of the former monthly scheduler."""
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _monthly_deploy(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: Any,
) -> list[dict[str, Any]]:
    """Deprecated compatibility adapter routed through the causal strategy interface."""
    return deploy_legacy_baseline(plan, events, candles, "monthly_dca", 0)


def _events_for_mode(
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
    capital_mode: str,
) -> tuple[CashFlowEvent, ...]:
    if capital_mode not in CAPITAL_MODES:
        raise ValueError(f"unsupported capital mode: {capital_mode}")
    events = contribution_schedule(plan, start, end)
    return events if capital_mode == "recurring_monthly_contributions" else events[:1]


def run_scenario_passive(
    *,
    data_dir: Path,
    pair: str,
    timeframe: str,
    timerange: str,
    capital_mode: str,
    initial_capital: Decimal | str,
    monthly_budget: Decimal | str,
    fee: Decimal | str,
    contribution_day: int,
    repository_commit: str,
    weekly_day: str = "monday",
    registry_path: Path = DEFAULT_STRATEGY_REGISTRY,
) -> dict[str, Any]:
    """Run registry-selected passive baselines with the active funding convention."""
    start, end = parse_timerange(timerange)
    plan = InvestmentPlan(initial_capital, monthly_budget, fee, contribution_day)
    if weekly_day.lower() not in WEEKDAYS:
        raise ValueError(f"unsupported weekly day: {weekly_day}")
    if not repository_commit.strip():
        raise ValueError("repository commit must be non-empty")
    events = _events_for_mode(plan, start, end, capital_mode)
    candles = load_kraken_candles(data_dir, pair, timeframe, timerange)
    registry = load_registry(registry_path)
    definitions = registered_baselines(
        registry,
        include_immediate=capital_mode == "one_shot_capital",
        include_monthly=True,
    )
    schedule = [
        {
            "contributed_at": event.contributed_at.isoformat(),
            "amount": number(event.amount),
            "kind": event.kind,
        }
        for event in events
    ]
    benchmarks = []
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
            repository_commit=repository_commit,
        )
        result["contribution_schedule"] = schedule
        benchmarks.append(result)
    total_contributions = sum((event.amount for event in events), Decimal("0"))
    payload = {
        "metadata": {
            "timerange": timerange,
            "timeframe": timeframe,
            "fee": number(plan.fee_ratio),
            "data_dir": str(data_dir),
            "pairs": [pair],
            "initial_capital": number(plan.initial_capital),
            "monthly_budget": number(plan.monthly_budget),
            "contribution_day": plan.contribution_day,
            "contribution_schedule": schedule,
            "total_contributions": number(total_contributions),
            "pair_candle_coverage": {pair: candle_metadata(candles, timerange)},
            "capital_mode": capital_mode,
            "repository_commit": repository_commit,
            "strategy_registry": {
                "registry_schema_version": registry.registry_schema_version,
                "registry_id": registry.registry_id,
                "registry_digest": registry.digest,
            },
        },
        "benchmarks": benchmarks,
    }
    return enrich_passive_result(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("user_data/data/kraken"),
    )
    parser.add_argument("--pair", required=True)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--timerange", required=True)
    parser.add_argument(
        "--capital-mode",
        required=True,
        choices=sorted(CAPITAL_MODES),
    )
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--fee", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--weekly-day", default="monday")
    parser.add_argument(
        "--strategy-registry",
        type=Path,
        default=DEFAULT_STRATEGY_REGISTRY,
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    payload = run_scenario_passive(
        data_dir=args.data_dir,
        pair=args.pair,
        timeframe=args.timeframe,
        timerange=args.timerange,
        capital_mode=args.capital_mode,
        initial_capital=args.initial_capital,
        monthly_budget=args.monthly_budget,
        fee=args.fee,
        contribution_day=args.contribution_day,
        repository_commit=args.repository_commit,
        weekly_day=args.weekly_day,
        registry_path=args.strategy_registry,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if args.output_dir:
        write_metrics_csv(payload, args.output_dir)


if __name__ == "__main__":
    main()
