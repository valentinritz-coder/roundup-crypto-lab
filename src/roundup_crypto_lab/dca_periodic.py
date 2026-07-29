"""Registry-backed passive DCA at deterministic multi-month intervals."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from roundup_crypto_lab.dca_decimal_safety import consume_fifo, exact_purchase
from roundup_crypto_lab.dca_registry import (
    MinimumOrderRule,
    StrategyDefinition,
    StrategyRegistry,
)
from roundup_crypto_lab.dca_strategy import (
    DcaBuyOrder,
    DcaDecision,
    DcaDecisionContext,
    PendingCashBucket,
    build_decision_context,
    evaluate_strategy,
)
from roundup_crypto_lab.deployment_engine import INTERVAL
from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan

PERIODIC_IMPLEMENTATION = "fixed_periodic"
_MIN_INTERVAL_MONTHS = 1
_MAX_INTERVAL_MONTHS = 3
_CALENDAR_ANCHOR_YEAR = 1970


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _periodicity(interval_months: object, phase_offset_months: object) -> tuple[int, int]:
    interval = _integer(interval_months, "interval_months")
    phase = _integer(phase_offset_months, "phase_offset_months")
    if not _MIN_INTERVAL_MONTHS <= interval <= _MAX_INTERVAL_MONTHS:
        raise ValueError(
            f"interval_months must be from {_MIN_INTERVAL_MONTHS} through "
            f"{_MAX_INTERVAL_MONTHS}"
        )
    if not 0 <= phase < interval:
        raise ValueError("phase_offset_months must be lower than interval_months")
    return interval, phase


def _month_index(year: int, month: int) -> int:
    return (year - _CALENDAR_ANCHOR_YEAR) * 12 + month - 1


def periodic_execution_dates(
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
    *,
    interval_months: int,
    phase_offset_months: int,
) -> tuple[datetime, ...]:
    """Return one UTC execution instant for each eligible calendar cycle.

    Calendar phases are anchored to January 1970. Phase zero therefore includes
    January for every supported interval, phase one includes February, and so on.
    The configured contribution day is clipped to the last day of short months.
    """
    if not isinstance(plan, InvestmentPlan):
        raise TypeError("plan must be an InvestmentPlan")
    start = _utc(start, "start")
    end = _utc(end, "end")
    if start >= end:
        raise ValueError("start must be strictly before end")
    interval, phase = _periodicity(interval_months, phase_offset_months)

    dates: list[datetime] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        if _month_index(year, month) % interval == phase:
            day = min(plan.contribution_day, monthrange(year, month)[1])
            scheduled_at = datetime(year, month, day, tzinfo=UTC)
            if start <= scheduled_at < end:
                dates.append(scheduled_at)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return tuple(dates)


def registered_periodic_strategies(
    registry: StrategyRegistry,
) -> tuple[StrategyDefinition, ...]:
    """Select periodic definitions in deterministic interval, phase and ID order."""
    if not isinstance(registry, StrategyRegistry):
        raise TypeError("registry must be a StrategyRegistry")
    definitions = [
        definition
        for definition in registry.strategies
        if definition.implementation == PERIODIC_IMPLEMENTATION
    ]
    return tuple(
        sorted(
            definitions,
            key=lambda definition: (
                int(definition.parameters["interval_months"]),
                int(definition.parameters["phase_offset_months"]),
                definition.strategy_id,
            ),
        )
    )


def _definition_parameters(definition: StrategyDefinition) -> tuple[int, int]:
    if not isinstance(definition, StrategyDefinition):
        raise TypeError("definition must be a StrategyDefinition")
    if definition.implementation != PERIODIC_IMPLEMENTATION:
        raise ValueError(
            f"periodic deployment requires {PERIODIC_IMPLEMENTATION}, got "
            f"{definition.implementation}"
        )
    if definition.required_indicators:
        raise ValueError("fixed periodic DCA does not accept indicators")
    expected = {"interval_months", "phase_offset_months"}
    if set(definition.parameters) != expected:
        raise ValueError("fixed periodic DCA requires interval and phase parameters")
    return _periodicity(
        definition.parameters["interval_months"],
        definition.parameters["phase_offset_months"],
    )


def _minimum_order(rule: MinimumOrderRule) -> Decimal:
    if not isinstance(rule, MinimumOrderRule):
        raise TypeError("minimum order must be a MinimumOrderRule")
    try:
        minimum = Decimal(rule.amount)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("minimum order amount must be a decimal") from exc
    if not minimum.is_finite() or minimum < 0:
        raise ValueError("minimum order amount must be finite and non-negative")
    if rule.behavior not in {"skip", "spend_available"}:
        raise ValueError("unsupported minimum order behavior")
    return minimum


@dataclass(frozen=True)
class FixedPeriodicDCA:
    """Invest all visible contributed cash at each eligible calendar decision."""

    definition: StrategyDefinition

    def __post_init__(self) -> None:
        _definition_parameters(self.definition)
        _minimum_order(self.definition.minimum_order)

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.definition.strategy_version

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        minimum = _minimum_order(self.definition.minimum_order)
        available = context.available_cash
        if available == 0:
            return DcaDecision(
                decision_tag="fixed_periodic.no-cash",
                diagnostics={
                    "available_cash": available,
                    "minimum_order": minimum,
                },
            )
        if available < minimum:
            return DcaDecision(
                decision_tag="fixed_periodic.below-minimum",
                diagnostics={
                    "available_cash": available,
                    "minimum_order": minimum,
                },
            )
        return DcaDecision(
            decision_tag="fixed_periodic.scheduled-buy",
            orders=(DcaBuyOrder(available, "all-available-cash"),),
            diagnostics={
                "available_cash": available,
                "minimum_order": minimum,
            },
        )


def _portfolio_state_at(
    purchases: Sequence[dict[str, Any]],
    decision_at: datetime,
    candles: pd.DataFrame,
) -> tuple[Decimal, Decimal, Decimal]:
    quantity = Decimal("0")
    fees = Decimal("0")
    for purchase in purchases:
        if datetime.fromisoformat(purchase["executed_at"]) <= decision_at:
            quantity += Decimal(str(purchase["quantity"]))
            fees += Decimal(str(purchase["fee_paid"]))
    eligible = candles[candles["date"] + pd.Timedelta(INTERVAL) <= decision_at]
    mark = Decimal("0") if eligible.empty else Decimal(str(eligible.iloc[-1]["close"]))
    return quantity, fees, quantity * mark


def deploy_registered_periodic(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    definition: StrategyDefinition,
) -> list[dict[str, Any]]:
    """Execute one all-cash buy per eligible cycle through the DCA contract.

    Contributions remain monthly. Cash below the configured minimum order, or cash
    without an eligible execution candle, stays in FIFO pending buckets until a
    later cycle. Every execution records the exact buckets that funded it.
    """
    if not isinstance(plan, InvestmentPlan):
        raise TypeError("plan must be an InvestmentPlan")
    interval, phase = _definition_parameters(definition)
    strategy = FixedPeriodicDCA(definition)
    if not events:
        raise ValueError("periodic deployment requires at least one contribution")
    if candles.empty:
        raise ValueError("periodic deployment requires at least one candle")

    ordered_events = tuple(sorted(events, key=lambda event: (event.contributed_at, event.kind)))
    if any(not isinstance(event, CashFlowEvent) for event in ordered_events):
        raise TypeError("events must contain CashFlowEvent values")
    if any(event.contributed_at.tzinfo is None for event in ordered_events):
        raise ValueError("contribution timestamps must be timezone-aware")

    start = ordered_events[0].contributed_at.astimezone(UTC)
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC) + INTERVAL
    decision_dates = periodic_execution_dates(
        plan,
        start,
        end,
        interval_months=interval,
        phase_offset_months=phase,
    )

    pending: list[list[Any]] = []
    purchases: list[dict[str, Any]] = []
    event_position = 0
    for decision_at in decision_dates:
        while (
            event_position < len(ordered_events)
            and ordered_events[event_position].contributed_at <= decision_at
        ):
            event = ordered_events[event_position]
            pending.append([event.contributed_at.astimezone(UTC), event.amount])
            event_position += 1
        available = sum((Decimal(str(bucket[1])) for bucket in pending), Decimal("0"))
        if available <= 0:
            continue

        quantity, fees, marked_asset_value = _portfolio_state_at(
            purchases,
            decision_at,
            candles,
        )
        context = build_decision_context(
            decision_at=decision_at,
            available_cash=available,
            quantity=quantity,
            marked_asset_value=marked_asset_value,
            cumulative_contributions=sum(
                (
                    event.amount
                    for event in ordered_events
                    if event.contributed_at <= decision_at
                ),
                Decimal("0"),
            ),
            cumulative_fees=fees,
            pending_cash=tuple(
                PendingCashBucket(bucket[0], Decimal(str(bucket[1])))
                for bucket in pending
            ),
            candles=candles,
        )
        decision = evaluate_strategy(strategy, context)
        if not decision.orders:
            continue

        order = decision.orders[0]
        funding_event = CashFlowEvent(pending[0][0], available, "periodic")
        execution = exact_purchase(
            candles,
            funding_event,
            decision_at,
            order.gross_amount,
            plan.fee_ratio,
        )
        if execution is None:
            continue
        execution["decision_tag"] = decision.decision_tag
        execution["order_tag"] = order.order_tag
        execution["interval_months"] = interval
        execution["phase_offset_months"] = phase
        execution["funding_allocations"] = consume_fifo(pending, order.gross_amount)
        purchases.append(execution)
    return purchases
