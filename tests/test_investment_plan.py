from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule


def test_schedule_is_start_inclusive_end_exclusive_and_deterministically_ordered() -> None:
    plan = InvestmentPlan(Decimal("200"), Decimal("40"), Decimal("0.004"), 23)
    events = contribution_schedule(
        plan, datetime(2026, 1, 23, tzinfo=UTC), datetime(2026, 3, 23, tzinfo=UTC)
    )
    assert [(event.kind, event.contributed_at.date(), event.amount) for event in events] == [
        ("initial", datetime(2026, 1, 23).date(), Decimal("200")),
        ("monthly", datetime(2026, 1, 23).date(), Decimal("40")),
        ("monthly", datetime(2026, 2, 23).date(), Decimal("40")),
    ]


def test_schedule_clips_invalid_calendar_days_including_leap_year() -> None:
    plan = InvestmentPlan(Decimal("1"), Decimal("40"), Decimal("0"), 31)
    events = contribution_schedule(
        plan, datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC)
    )
    assert [event.contributed_at.date().isoformat() for event in events] == [
        "2024-02-01",
        "2024-02-29",
        "2024-03-31",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_capital": Decimal("0")},
        {"monthly_budget": Decimal("-1")},
        {"fee_ratio": Decimal("1")},
        {"contribution_day": 32},
    ],
)
def test_plan_rejects_invalid_arguments(kwargs: dict[str, object]) -> None:
    baseline = {
        "initial_capital": Decimal("1"),
        "monthly_budget": Decimal("1"),
        "fee_ratio": Decimal("0"),
    }
    baseline.update(kwargs)
    with pytest.raises(ValueError):
        InvestmentPlan(**baseline)
