"""Profile-aware passive DCA executors with carry-forward minimum orders."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from roundup_crypto_lab.dca_baselines import allocation_schedule, effective_parameters
from roundup_crypto_lab.dca_decimal_safety import consume_fifo
from roundup_crypto_lab.dca_periodic import periodic_execution_dates
from roundup_crypto_lab.dca_registry import StrategyDefinition, StrategyRegistry
from roundup_crypto_lab.deployment_engine import INTERVAL, build_result
from roundup_crypto_lab.execution_costs import (
    ExecutionCostProfile,
    enrich_result_with_execution_costs,
    execute_costed_purchase,
)
from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan

FREQUENCY_IMPLEMENTATIONS = frozenset(
    {"fixed_weekly", "fixed_monthly", "fixed_periodic"}
)


def _definition_minimum(definition: StrategyDefinition) -> Decimal:
    minimum = Decimal(definition.minimum_order.amount)
    if not minimum.is_finite() or minimum < 0:
        raise ValueError("strategy minimum order must be finite and non-negative")
    return minimum


def _effective_minimum(
    definition: StrategyDefinition,
    profile: ExecutionCostProfile,
) -> Decimal:
    return max(_definition_minimum(definition), profile.minimum_order_amount)


def _append_pending(
    pending: list[list[Any]],
    contributed_at: datetime,
    amount: Decimal,
) -> None:
    contributed_at = contributed_at.astimezone(UTC)
    if amount <= 0:
        return
    for bucket in pending:
        if bucket[0] == contributed_at:
            bucket[1] += amount
            return
    pending.append([contributed_at, amount])
    pending.sort(key=lambda bucket: bucket[0])


def _available(pending: list[list[Any]]) -> Decimal:
    return sum((Decimal(str(bucket[1])) for bucket in pending), Decimal("0"))


def deploy_costed_baseline(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    definition: StrategyDefinition,
    profile: ExecutionCostProfile,
    *,
    parameter_overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute weekly or monthly fixed schedules with profile-level carry-forward."""
    if definition.implementation not in {"fixed_weekly", "fixed_monthly"}:
        raise ValueError("costed baseline executor supports weekly or monthly DCA")
    if not isinstance(profile, ExecutionCostProfile):
        raise TypeError("profile must be an ExecutionCostProfile")
    parameters = effective_parameters(definition, parameter_overrides)
    allocations = sorted(
        allocation_schedule(
            definition,
            events,
            candles,
            parameter_overrides=parameters,
        ),
        key=lambda item: (item.scheduled_at, item.contributed_at),
    )
    minimum = _effective_minimum(definition, profile)
    pending: list[list[Any]] = []
    purchases: list[dict[str, Any]] = []

    for allocation in allocations:
        _append_pending(
            pending,
            allocation.contributed_at,
            allocation.gross_amount,
        )
        available = _available(pending)
        if available < minimum or not profile.can_execute(available):
            continue
        funding_event = CashFlowEvent(
            pending[0][0],
            available,
            "cost-profile-carry-forward",
        )
        execution = execute_costed_purchase(
            candles,
            funding_event,
            allocation.scheduled_at,
            available,
            profile,
        )
        if execution is None:
            continue
        execution["decision_tag"] = (
            f"{definition.implementation}.cost-profile-scheduled-buy"
        )
        execution["order_tag"] = "all-eligible-cash"
        execution["funding_allocations"] = consume_fifo(pending, available)
        purchases.append(execution)
    return purchases


def _periodic_parameters(definition: StrategyDefinition) -> tuple[int, int]:
    if definition.implementation != "fixed_periodic":
        raise ValueError("periodic cost executor requires fixed_periodic")
    expected = {"interval_months", "phase_offset_months"}
    if set(definition.parameters) != expected:
        raise ValueError("fixed_periodic requires interval and phase parameters")
    interval = definition.parameters["interval_months"]
    phase = definition.parameters["phase_offset_months"]
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise ValueError("interval_months must be an integer")
    if isinstance(phase, bool) or not isinstance(phase, int):
        raise ValueError("phase_offset_months must be an integer")
    if not 1 <= interval <= 3 or not 0 <= phase < interval:
        raise ValueError("invalid fixed_periodic interval or phase")
    return interval, phase


def deploy_costed_periodic(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    definition: StrategyDefinition,
    profile: ExecutionCostProfile,
) -> list[dict[str, Any]]:
    """Invest all contributed cash at each eligible multi-month cycle."""
    if not events:
        raise ValueError("periodic cost execution requires contribution events")
    if candles.empty:
        raise ValueError("periodic cost execution requires candles")
    interval, phase = _periodic_parameters(definition)
    minimum = _effective_minimum(definition, profile)
    ordered_events = tuple(
        sorted(events, key=lambda event: (event.contributed_at, event.kind))
    )
    start = ordered_events[0].contributed_at.astimezone(UTC)
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC) + INTERVAL
    decisions = periodic_execution_dates(
        plan,
        start,
        end,
        interval_months=interval,
        phase_offset_months=phase,
    )

    pending: list[list[Any]] = []
    purchases: list[dict[str, Any]] = []
    event_position = 0
    for decision_at in decisions:
        while (
            event_position < len(ordered_events)
            and ordered_events[event_position].contributed_at <= decision_at
        ):
            event = ordered_events[event_position]
            _append_pending(pending, event.contributed_at, event.amount)
            event_position += 1
        available = _available(pending)
        if available < minimum or not profile.can_execute(available):
            continue
        funding_event = CashFlowEvent(
            pending[0][0],
            available,
            "cost-profile-periodic",
        )
        execution = execute_costed_purchase(
            candles,
            funding_event,
            decision_at,
            available,
            profile,
        )
        if execution is None:
            continue
        execution["decision_tag"] = "fixed_periodic.cost-profile-scheduled-buy"
        execution["order_tag"] = "all-available-cash"
        execution["interval_months"] = interval
        execution["phase_offset_months"] = phase
        execution["funding_allocations"] = consume_fifo(pending, available)
        purchases.append(execution)
    return purchases


def registered_frequency_strategies(
    registry: StrategyRegistry,
) -> tuple[StrategyDefinition, ...]:
    """Select weekly, monthly and periodic definitions in deterministic order."""
    selected = [
        definition
        for definition in registry.strategies
        if definition.implementation in FREQUENCY_IMPLEMENTATIONS
    ]
    weekly = [
        definition
        for definition in selected
        if definition.implementation == "fixed_weekly"
    ]
    monthly = [
        definition
        for definition in selected
        if definition.implementation == "fixed_monthly"
    ]
    if len(weekly) != 1 or len(monthly) != 1:
        raise ValueError(
            "frequency registry must define exactly one weekly and one monthly baseline"
        )
    periodic = sorted(
        (
            definition
            for definition in selected
            if definition.implementation == "fixed_periodic"
        ),
        key=lambda definition: (
            int(definition.parameters["interval_months"]),
            int(definition.parameters["phase_offset_months"]),
            definition.strategy_id,
        ),
    )
    return (weekly[0], monthly[0], *periodic)


def result_name(definition: StrategyDefinition) -> str:
    if definition.implementation == "fixed_weekly":
        return "WeeklyDCA"
    if definition.implementation == "fixed_monthly":
        return "MonthlyDCA"
    return definition.strategy_id


def run_costed_strategy(
    *,
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    pair: str,
    definition: StrategyDefinition,
    profile: ExecutionCostProfile,
    weekly_day: int,
) -> dict[str, Any]:
    if definition.implementation == "fixed_periodic":
        purchases = deploy_costed_periodic(
            plan,
            events,
            candles,
            definition,
            profile,
        )
        parameters = dict(definition.parameters)
    else:
        overrides = (
            {"weekday": weekly_day}
            if definition.implementation == "fixed_weekly"
            else None
        )
        purchases = deploy_costed_baseline(
            plan,
            events,
            candles,
            definition,
            profile,
            parameter_overrides=overrides,
        )
        parameters = effective_parameters(definition, overrides)

    result = build_result(
        result_name(definition),
        pair,
        candles,
        events,
        purchases,
    )
    result["strategy"] = {
        "strategy_id": definition.strategy_id,
        "strategy_version": definition.strategy_version,
        "implementation": definition.implementation,
        "implementation_identity": definition.implementation_identity,
        "parameters": parameters,
    }
    enrich_result_with_execution_costs(result, purchases, profile)
    return result
