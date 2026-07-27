from __future__ import annotations

from calendar import monthrange
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from roundup_crypto_lab.dca_baselines import (
    baseline_name,
    deploy_registered_baseline,
    deployment_method,
    registered_baselines,
    strategy_metadata,
)
from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.deployment_engine import (
    INTERVAL,
    build_result,
    deployment_buckets,
    deployment_dates,
    purchase,
)
from roundup_crypto_lab.investment_plan import (
    CashFlowEvent,
    InvestmentPlan,
    contribution_schedule,
)
from roundup_crypto_lab.passive_cash_flow_reporting import enrich_passive_result

REGISTRY = Path("config/dca-strategy-registry.json")


def _candles(
    start: datetime,
    end: datetime,
    *,
    missing: frozenset[datetime] = frozenset(),
) -> pd.DataFrame:
    dates = []
    current = start
    while current < end:
        if current not in missing:
            dates.append(current)
        current += INTERVAL
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "open": [Decimal("100") + index for index in range(len(dates))],
            "high": [Decimal("101") + index for index in range(len(dates))],
            "low": [Decimal("99") + index for index in range(len(dates))],
            "close": [Decimal("100.5") + index for index in range(len(dates))],
            "volume": [Decimal("1")] * len(dates),
        }
    )


def _next_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(
        year=year,
        month=month,
        day=min(value.day, monthrange(year, month)[1]),
    )


def _legacy_reference_deploy(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    method: str,
    weekly_day: int,
) -> list[dict[str, object]]:
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC) + INTERVAL
    purchases = []
    buckets = deployment_buckets(events)
    for position, bucket in enumerate(buckets):
        next_at = (
            buckets[position + 1].contributed_at
            if position + 1 < len(buckets)
            else end
        )
        if method == "immediate":
            scheduled = [bucket.contributed_at]
        elif method in {"daily_dca", "weekly_dca"}:
            scheduled = deployment_dates(
                bucket.contributed_at,
                next_at,
                method,
                weekly_day,
            )
        elif method == "monthly_dca":
            scheduled = []
            current = bucket.contributed_at
            while current < next_at:
                scheduled.append(current)
                current = _next_month(current)
        else:
            raise ValueError(f"unsupported reference method: {method}")
        if not scheduled:
            continue
        portion = bucket.amount / len(scheduled)
        amounts = [portion] * (len(scheduled) - 1)
        amounts.append(bucket.amount - sum(amounts, Decimal("0")))
        event = CashFlowEvent(bucket.contributed_at, bucket.amount, "deployment")
        for scheduled_at, amount in zip(scheduled, amounts, strict=True):
            executed = purchase(
                candles,
                event,
                scheduled_at,
                amount,
                plan.fee_ratio,
            )
            if executed is not None:
                purchases.append(executed)
    return purchases


def _cash_flow_payload(
    result: dict[str, object],
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    timerange: str,
) -> dict[str, object]:
    schedule = [
        {
            "contributed_at": event.contributed_at.isoformat(),
            "amount": float(event.amount),
            "kind": event.kind,
        }
        for event in events
    ]
    return {
        "metadata": {
            "timerange": timerange,
            "initial_capital": float(plan.initial_capital),
            "monthly_budget": float(plan.monthly_budget),
            "fee": float(plan.fee_ratio),
            "contribution_schedule": schedule,
        },
        "benchmarks": [result],
    }


@pytest.mark.parametrize(
    ("timerange", "contribution_day", "one_shot", "missing"),
    [
        ("20260117-20260305", 31, False, frozenset()),
        (
            "20240220-20240303",
            29,
            False,
            frozenset(
                {
                    datetime(2024, 2, 20, tzinfo=UTC),
                    datetime(2024, 2, 26, tzinfo=UTC),
                }
            ),
        ),
        ("20260527-20260611", 7, True, frozenset()),
    ],
)
def test_registered_baselines_are_economically_identical_to_legacy_methods(
    timerange: str,
    contribution_day: int,
    one_shot: bool,
    missing: frozenset[datetime],
) -> None:
    start = datetime.strptime(timerange[:8], "%Y%m%d").replace(tzinfo=UTC)
    end = datetime.strptime(timerange[9:], "%Y%m%d").replace(tzinfo=UTC)
    plan = InvestmentPlan("200", "40", "0.004", contribution_day)
    events = contribution_schedule(plan, start, end)
    if one_shot:
        events = events[:1]
    candles = _candles(start, end, missing=missing)
    registry = load_registry(REGISTRY)
    definitions = registered_baselines(
        registry,
        include_immediate=one_shot,
        include_monthly=True,
    )

    for definition in definitions:
        overrides = {"weekday": 2} if definition.implementation == "fixed_weekly" else None
        new_purchases = deploy_registered_baseline(
            plan,
            events,
            candles,
            definition,
            parameter_overrides=overrides,
        )
        old_purchases = _legacy_reference_deploy(
            plan,
            events,
            candles,
            deployment_method(definition),
            2,
        )
        assert new_purchases == old_purchases

        name = baseline_name(definition)
        new_result = build_result(
            name,
            "BTC/EUR",
            candles,
            events,
            deepcopy(new_purchases),
        )
        old_result = build_result(
            name,
            "BTC/EUR",
            candles,
            events,
            deepcopy(old_purchases),
        )
        assert new_result == old_result

        new_payload = enrich_passive_result(
            _cash_flow_payload(new_result, plan, events, timerange)
        )
        old_payload = enrich_passive_result(
            _cash_flow_payload(old_result, plan, events, timerange)
        )
        assert new_payload["benchmarks"][0]["cash_flow_metrics"] == (
            old_payload["benchmarks"][0]["cash_flow_metrics"]
        )


def test_registry_selection_preserves_historical_output_order() -> None:
    registry = load_registry(REGISTRY)
    definitions = registered_baselines(
        registry,
        include_immediate=True,
        include_monthly=True,
    )
    assert [baseline_name(definition) for definition in definitions] == [
        "BuyAndHold",
        "DailyDCA",
        "WeeklyDCA",
        "MonthlyDCA",
    ]


def test_weekday_override_is_explicit_in_strategy_metadata() -> None:
    registry = load_registry(REGISTRY)
    weekly = registry.strategy("weekly-dca")
    metadata = strategy_metadata(
        registry,
        weekly,
        parameter_overrides={"weekday": 4},
        repository_commit="a" * 40,
    )
    assert metadata["parameters"] == {"weekday": 4}
    assert metadata["repository_commit"] == "a" * 40
