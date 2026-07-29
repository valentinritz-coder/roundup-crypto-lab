from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.dca_baselines import deploy_registered_baseline
from roundup_crypto_lab.dca_periodic import (
    deploy_registered_periodic,
    periodic_execution_dates,
    registered_periodic_strategies,
)
from roundup_crypto_lab.dca_registry import load_registry, parse_registry
from roundup_crypto_lab.deployment_engine import INTERVAL, build_result
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule


def _registry_strategy(
    interval_months: object,
    phase_offset_months: object,
    *,
    strategy_id: str = "periodic-dca",
    minimum_order: str = "0",
):
    registry = parse_registry(
        {
            "registry_schema_version": 1,
            "registry_id": "periodic-test-registry",
            "strategies": [
                {
                    "strategy_id": strategy_id,
                    "implementation": "fixed_periodic",
                    "strategy_version": "1",
                    "hypothesis": "Deploy all pending cash on fixed calendar cycles.",
                    "decision_cadence": "monthly",
                    "required_indicators": [],
                    "parameters": {
                        "interval_months": interval_months,
                        "phase_offset_months": phase_offset_months,
                    },
                    "maximum_pending_cash_age_days": None,
                    "minimum_order": {
                        "amount": minimum_order,
                        "behavior": "skip",
                    },
                    "research_status": "baseline",
                }
            ],
        }
    )
    return registry, registry.strategy(strategy_id)


def _candles(start: datetime, end: datetime, *, price: str = "100") -> pd.DataFrame:
    dates = []
    current = start
    while current < end:
        dates.append(current)
        current += INTERVAL
    value = Decimal(price)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "open": [value] * len(dates),
            "high": [value + 1] * len(dates),
            "low": [value - 1] * len(dates),
            "close": [value] * len(dates),
            "volume": [Decimal("1")] * len(dates),
        }
    )


@pytest.mark.parametrize(
    ("interval", "phase", "months"),
    [
        (1, 0, [1, 2, 3, 4, 5, 6]),
        (2, 0, [1, 3, 5]),
        (2, 1, [2, 4, 6]),
        (3, 0, [1, 4]),
        (3, 1, [2, 5]),
        (3, 2, [3, 6]),
    ],
)
def test_periodic_execution_dates_cover_every_supported_phase(
    interval: int,
    phase: int,
    months: list[int],
) -> None:
    plan = InvestmentPlan("40", "40", "0.0026", 10)
    dates = periodic_execution_dates(
        plan,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
        interval_months=interval,
        phase_offset_months=phase,
    )

    assert [date.month for date in dates] == months
    assert all(date.day == 10 for date in dates)
    assert len(dates) == len(set(dates))


def test_mid_cycle_timerange_waits_for_the_next_eligible_phase() -> None:
    plan = InvestmentPlan("40", "40", "0", 10)

    dates = periodic_execution_dates(
        plan,
        datetime(2026, 2, 20, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
        interval_months=3,
        phase_offset_months=0,
    )

    assert dates == (
        datetime(2026, 4, 10, tzinfo=UTC),
        datetime(2026, 7, 10, tzinfo=UTC),
    )


def test_pending_monthly_cash_accumulates_and_deploys_in_one_order() -> None:
    start = datetime(2026, 2, 20, tzinfo=UTC)
    end = datetime(2026, 6, 1, tzinfo=UTC)
    plan = InvestmentPlan("40", "40", "0.0026", 10)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end)
    _, definition = _registry_strategy(3, 0)

    purchases = deploy_registered_periodic(plan, events, candles, definition)
    result = build_result("QuarterlyDCA", "BTC/EUR", candles, events, purchases)

    assert len(purchases) == 1
    assert purchases[0]["scheduled_at"] == datetime(2026, 4, 10, tzinfo=UTC).isoformat()
    assert purchases[0]["gross_contribution"] == Decimal("120")
    assert purchases[0]["funding_allocations"] == [
        {"contributed_at": datetime(2026, 2, 20, tzinfo=UTC).isoformat(), "amount": "40"},
        {"contributed_at": datetime(2026, 3, 10, tzinfo=UTC).isoformat(), "amount": "40"},
        {"contributed_at": datetime(2026, 4, 10, tzinfo=UTC).isoformat(), "amount": "40"},
    ]
    assert result["cash_balance_exact"] == "40"


def test_minimum_order_skips_and_carries_cash_to_later_cycles() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 5, 1, tzinfo=UTC)
    plan = InvestmentPlan("0.4", "0.4", "0", 10)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end)
    _, definition = _registry_strategy(1, 0, minimum_order="1")

    purchases = deploy_registered_periodic(plan, events, candles, definition)
    result = build_result("MonthlyPeriodicDCA", "BTC/EUR", candles, events, purchases)

    assert [purchase["gross_contribution"] for purchase in purchases] == [Decimal("1.2")]
    assert purchases[0]["scheduled_at"] == datetime(2026, 2, 10, tzinfo=UTC).isoformat()
    assert result["cash_balance_exact"] == "0.8"


def test_monthly_periodic_is_economically_equivalent_to_monthly_baseline() -> None:
    start = datetime(2026, 1, 10, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)
    plan = InvestmentPlan("40", "40", "0.0026", 10)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end)
    monthly = load_registry("config/dca-strategy-registry.json").strategy("monthly-dca")
    _, periodic = _registry_strategy(1, 0)

    baseline_purchases = deploy_registered_baseline(plan, events, candles, monthly)
    periodic_purchases = deploy_registered_periodic(plan, events, candles, periodic)
    baseline = build_result(
        "MonthlyDCA",
        "BTC/EUR",
        candles,
        events,
        baseline_purchases,
    )
    candidate = build_result(
        "MonthlyPeriodicDCA",
        "BTC/EUR",
        candles,
        events,
        periodic_purchases,
    )

    for field in (
        "capital_invested",
        "cash_balance_exact",
        "fees_paid",
        "quantity_exact",
        "final_value_exact",
    ):
        assert candidate[field] == baseline[field]


def test_periodic_execution_preserves_exact_decimal_purchase_identities() -> None:
    start = datetime(2026, 1, 10, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    amount = "0.1234567890123456789012345678"
    plan = InvestmentPlan(amount, amount, "0.0026", 10)
    events = contribution_schedule(plan, start, end)
    candles = _candles(start, end, price="137.11")
    _, definition = _registry_strategy(1, 0)

    purchases = deploy_registered_periodic(plan, events, candles, definition)
    result = build_result("MonthlyPeriodicDCA", "BTC/EUR", candles, events, purchases)

    assert len(purchases) == 1
    assert purchases[0]["gross_contribution"] == (
        purchases[0]["fee_paid"] + purchases[0]["net_contribution"]
    )
    assert purchases[0]["quantity"] == (
        purchases[0]["net_contribution"] / purchases[0]["execution_price"]
    )
    assert result["cash_balance_exact"] == "0"


@pytest.mark.parametrize(
    ("interval", "phase", "match"),
    [
        (0, 0, "at least 1"),
        (4, 0, "at most 3"),
        (2, 2, "lower than interval_months"),
        (3, -1, "at least 0"),
        (True, 0, "not a boolean"),
        (2, True, "not a boolean"),
    ],
)
def test_registry_rejects_invalid_periodic_parameters(
    interval: object,
    phase: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _registry_strategy(interval, phase)


def test_registered_periodic_strategies_have_deterministic_frequency_order() -> None:
    rows = []
    for strategy_id, interval, phase in (
        ("quarterly-phase-2", 3, 2),
        ("monthly", 1, 0),
        ("bimonthly-phase-1", 2, 1),
        ("quarterly-phase-0", 3, 0),
        ("bimonthly-phase-0", 2, 0),
    ):
        _, definition = _registry_strategy(
            interval,
            phase,
            strategy_id=strategy_id,
        )
        rows.append(
            {
                "strategy_id": definition.strategy_id,
                "implementation": definition.implementation,
                "strategy_version": definition.strategy_version,
                "hypothesis": definition.hypothesis,
                "decision_cadence": definition.decision_cadence,
                "required_indicators": [],
                "parameters": dict(definition.parameters),
                "maximum_pending_cash_age_days": None,
                "minimum_order": {"amount": "0", "behavior": "skip"},
                "research_status": "baseline",
            }
        )
    registry = parse_registry(
        {
            "registry_schema_version": 1,
            "registry_id": "periodic-order-test",
            "strategies": rows,
        }
    )

    assert [
        definition.strategy_id
        for definition in registered_periodic_strategies(registry)
    ] == [
        "monthly",
        "bimonthly-phase-0",
        "bimonthly-phase-1",
        "quarterly-phase-0",
        "quarterly-phase-2",
    ]
