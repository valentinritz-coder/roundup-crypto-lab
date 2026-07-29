from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from roundup_crypto_lab.dca_costed_execution import (
    deploy_costed_baseline,
    deploy_costed_periodic,
    registered_frequency_strategies,
    run_costed_strategy,
)
from roundup_crypto_lab.dca_registry import load_registry, parse_registry
from roundup_crypto_lab.execution_costs import ExecutionCostProfile
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule


def _candles(start: datetime, end: datetime) -> pd.DataFrame:
    dates = []
    current = start
    while current < end:
        dates.append(current)
        current += pd.Timedelta(hours=4)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "open": [Decimal("100")] * len(dates),
            "high": [Decimal("101")] * len(dates),
            "low": [Decimal("99")] * len(dates),
            "close": [Decimal("100")] * len(dates),
            "volume": [Decimal("1")] * len(dates),
        }
    )


def _profile(*, minimum: str = "1") -> ExecutionCostProfile:
    return ExecutionCostProfile(
        cost_profile_id="carry-forward-v1",
        profile_version=1,
        description="Carry-forward test profile.",
        profile_kind="baseline",
        trading_fee_ratio="0.0026",
        half_spread_ratio="0.0005",
        fixed_order_fee="0",
        minimum_order_amount=minimum,
        below_minimum_behavior="carry_forward",
    )


def _registry():
    return parse_registry(
        {
            "registry_schema_version": 1,
            "registry_id": "costed-frequency-test",
            "strategies": [
                {
                    "strategy_id": "weekly-dca",
                    "implementation": "fixed_weekly",
                    "strategy_version": "1",
                    "hypothesis": "Weekly control.",
                    "decision_cadence": "weekly",
                    "required_indicators": [],
                    "parameters": {"weekday": 0},
                    "maximum_pending_cash_age_days": None,
                    "minimum_order": {"amount": "0", "behavior": "skip"},
                    "research_status": "baseline",
                },
                {
                    "strategy_id": "monthly-dca",
                    "implementation": "fixed_monthly",
                    "strategy_version": "1",
                    "hypothesis": "Monthly control.",
                    "decision_cadence": "monthly",
                    "required_indicators": [],
                    "parameters": {},
                    "maximum_pending_cash_age_days": None,
                    "minimum_order": {"amount": "0", "behavior": "skip"},
                    "research_status": "baseline",
                },
                {
                    "strategy_id": "every-two-months-phase-0",
                    "implementation": "fixed_periodic",
                    "strategy_version": "1",
                    "hypothesis": "Two-month passive cycle.",
                    "decision_cadence": "monthly",
                    "required_indicators": [],
                    "parameters": {
                        "interval_months": 2,
                        "phase_offset_months": 0,
                    },
                    "maximum_pending_cash_age_days": None,
                    "minimum_order": {"amount": "0", "behavior": "skip"},
                    "research_status": "baseline",
                },
            ],
        }
    )


def test_monthly_minimum_order_carries_cash_forward() -> None:
    start = datetime(2026, 1, 15, tzinfo=UTC)
    end = datetime(2026, 3, 2, tzinfo=UTC)
    plan = InvestmentPlan("0.60", "0.60", "0.0026", 1)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end)
    monthly = _registry().strategy("monthly-dca")
    purchases = deploy_costed_baseline(
        plan,
        events,
        candles,
        monthly,
        _profile(minimum="1"),
    )
    assert len(purchases) == 1
    assert purchases[0]["scheduled_at"].startswith("2026-02-01")
    assert purchases[0]["gross_contribution"] == Decimal("1.20")
    assert [row["amount"] for row in purchases[0]["funding_allocations"]] == [
        "0.6",
        "0.6",
    ]


def test_periodic_executor_deploys_all_pending_cash_once_per_cycle() -> None:
    start = datetime(2026, 1, 15, tzinfo=UTC)
    end = datetime(2026, 7, 2, tzinfo=UTC)
    plan = InvestmentPlan("10", "10", "0.0026", 1)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end)
    definition = _registry().strategy("every-two-months-phase-0")
    purchases = deploy_costed_periodic(
        plan,
        events,
        candles,
        definition,
        _profile(),
    )
    scheduled = [row["scheduled_at"][:10] for row in purchases]
    assert scheduled == ["2026-03-01", "2026-05-01", "2026-07-01"]
    assert [row["gross_contribution"] for row in purchases] == [
        Decimal("30"),
        Decimal("20"),
        Decimal("20"),
    ]
    assert all(row["phase_offset_months"] == 0 for row in purchases)


def test_costed_result_reports_separate_cost_components() -> None:
    start = datetime(2026, 1, 15, tzinfo=UTC)
    end = datetime(2026, 3, 2, tzinfo=UTC)
    plan = InvestmentPlan("40", "40", "0.0026", 1)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end)
    monthly = _registry().strategy("monthly-dca")
    result = run_costed_strategy(
        plan=plan,
        events=events,
        candles=candles,
        pair="BTC/EUR",
        definition=monthly,
        profile=_profile(),
        weekly_day=0,
    )
    costs = result["execution_costs"]
    assert costs["order_count"] == result["number_of_buys"]
    assert Decimal(costs["explicit_fees_paid"]) == Decimal(str(result["fees_paid"]))
    assert Decimal(costs["estimated_spread_cost"]) > 0
    assert Decimal(costs["total_execution_cost"]) == (
        Decimal(costs["explicit_fees_paid"])
        + Decimal(costs["estimated_spread_cost"])
    )
    for row in result["purchase_ledger"]:
        assert {
            "reference_price",
            "execution_price",
            "trading_fee_paid",
            "fixed_order_fee_paid",
            "estimated_spread_cost",
            "net_notional",
        } <= set(row)


def test_frequency_registry_order_is_deterministic() -> None:
    definitions = registered_frequency_strategies(_registry())
    assert [definition.strategy_id for definition in definitions] == [
        "weekly-dca",
        "monthly-dca",
        "every-two-months-phase-0",
    ]


def test_repository_registry_remains_compatible() -> None:
    registry = load_registry(Path("config/dca-strategy-registry.json"))
    definitions = registered_frequency_strategies(registry)
    assert [definition.strategy_id for definition in definitions] == [
        "weekly-dca",
        "monthly-dca",
    ]
