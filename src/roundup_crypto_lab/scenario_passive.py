"""Run passive methods under the same one-shot or recurring scenario as active results."""

from __future__ import annotations

import argparse
import json
from calendar import monthrange
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan, contribution_schedule
from roundup_crypto_lab.passive_benchmarks import (
    INTERVAL,
    WEEKDAYS,
    _build_result,
    _candle_metadata,
    _deploy,
    _number,
    _purchase,
    deployment_buckets,
    load_kraken_candles,
    parse_timerange,
)
from roundup_crypto_lab.passive_cash_flow_reporting import enrich_passive_result, write_metrics_csv

CAPITAL_MODES = frozenset({"one_shot_capital", "recurring_monthly_contributions"})


def _next_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _monthly_deploy(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: Any,
) -> list[dict[str, Any]]:
    """Split each available funding bucket over monthly dates until the next contribution."""
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC) + INTERVAL
    purchases: list[dict[str, Any]] = []
    buckets = deployment_buckets(events)
    for position, bucket in enumerate(buckets):
        next_at = buckets[position + 1].contributed_at if position + 1 < len(buckets) else end
        scheduled: list[datetime] = []
        current = bucket.contributed_at
        while current < next_at:
            scheduled.append(current)
            current = _next_month(current)
        if not scheduled:
            continue
        portion = bucket.amount / len(scheduled)
        amounts = [portion] * (len(scheduled) - 1)
        amounts.append(bucket.amount - sum(amounts, Decimal("0")))
        event = CashFlowEvent(bucket.contributed_at, bucket.amount, "deployment")
        for scheduled_at, amount in zip(scheduled, amounts, strict=True):
            purchase = _purchase(candles, event, scheduled_at, amount, plan.fee_ratio)
            if purchase is not None:
                purchases.append(purchase)
    return purchases


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
) -> dict[str, Any]:
    """Run passive alternatives with the active scenario's exact funding convention."""
    start, end = parse_timerange(timerange)
    plan = InvestmentPlan(initial_capital, monthly_budget, fee, contribution_day)
    if weekly_day.lower() not in WEEKDAYS:
        raise ValueError(f"unsupported weekly day: {weekly_day}")
    if not repository_commit.strip():
        raise ValueError("repository commit must be non-empty")
    events = _events_for_mode(plan, start, end, capital_mode)
    candles = load_kraken_candles(data_dir, pair, timeframe, timerange)
    schedule = [
        {
            "contributed_at": event.contributed_at.isoformat(),
            "amount": _number(event.amount),
            "kind": event.kind,
        }
        for event in events
    ]
    methods = [
        ("DailyDCA", "daily_dca"),
        ("WeeklyDCA", "weekly_dca"),
        ("MonthlyDCA", "monthly_dca"),
    ]
    if capital_mode == "one_shot_capital":
        methods.insert(0, ("BuyAndHold", "immediate"))
    benchmarks = []
    for name, method in methods:
        if method == "monthly_dca":
            purchases = _monthly_deploy(plan, events, candles)
        else:
            purchases = _deploy(plan, events, candles, method, WEEKDAYS[weekly_day.lower()])
        result = _build_result(name, pair, candles, events, purchases)
        result["deployment_method"] = method
        result["contribution_schedule"] = schedule
        benchmarks.append(result)
    payload = {
        "metadata": {
            "timerange": timerange,
            "timeframe": timeframe,
            "fee": _number(plan.fee_ratio),
            "data_dir": str(data_dir),
            "pairs": [pair],
            "initial_capital": _number(plan.initial_capital),
            "monthly_budget": _number(plan.monthly_budget),
            "contribution_day": plan.contribution_day,
            "contribution_schedule": schedule,
            "total_contributions": _number(sum((event.amount for event in events), Decimal("0"))),
            "pair_candle_coverage": {pair: _candle_metadata(candles, timerange)},
            "capital_mode": capital_mode,
            "repository_commit": repository_commit,
        },
        "benchmarks": benchmarks,
    }
    return enrich_passive_result(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/kraken"))
    parser.add_argument("--pair", required=True)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--timerange", required=True)
    parser.add_argument("--capital-mode", required=True, choices=sorted(CAPITAL_MODES))
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--fee", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--weekly-day", default="monday")
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
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if args.output_dir:
        write_metrics_csv(payload, args.output_dir)


if __name__ == "__main__":
    main()
