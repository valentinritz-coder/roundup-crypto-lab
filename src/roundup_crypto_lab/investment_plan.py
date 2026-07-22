"""Investor cash-flow plan primitives for reproducible capital deployment."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


def as_decimal(value: Decimal | str, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return result


@dataclass(frozen=True)
class InvestmentPlan:
    """Immutable investor funding terms; all monetary fields are exact ``Decimal`` values."""

    initial_capital: Decimal
    monthly_budget: Decimal
    fee_ratio: Decimal
    contribution_day: int = 23

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "initial_capital", as_decimal(self.initial_capital, "initial capital")
        )
        object.__setattr__(
            self, "monthly_budget", as_decimal(self.monthly_budget, "monthly budget")
        )
        object.__setattr__(
            self, "fee_ratio", as_decimal(self.fee_ratio, "fee ratio", allow_zero=True)
        )
        if self.fee_ratio >= 1:
            raise ValueError("fee ratio must be lower than 1")
        if (
            not isinstance(self.contribution_day, int)
            or isinstance(self.contribution_day, bool)
            or not 1 <= self.contribution_day <= 31
        ):
            raise ValueError("contribution day must be an integer from 1 through 31")


@dataclass(frozen=True)
class CashFlowEvent:
    contributed_at: datetime
    amount: Decimal
    kind: str


def contribution_schedule(
    plan: InvestmentPlan, start: datetime, end: datetime
) -> tuple[CashFlowEvent, ...]:
    """Return chronological funding events in ``[start, end)`` UTC.

    A requested day absent from a month is clipped to that month's last calendar day.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("timerange timestamps must be timezone-aware")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if start >= end:
        raise ValueError("timerange start date must be strictly before end date")
    events = [CashFlowEvent(start, plan.initial_capital, "initial")]
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        day = min(plan.contribution_day, monthrange(year, month)[1])
        at = datetime(year, month, day, tzinfo=UTC)
        if start <= at < end:
            events.append(CashFlowEvent(at, plan.monthly_budget, "monthly"))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return tuple(sorted(events, key=lambda event: (event.contributed_at, event.kind)))
