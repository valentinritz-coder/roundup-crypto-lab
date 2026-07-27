"""Registry-backed fixed DCA baselines executed through the causal strategy contract."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

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
    execute_decision,
)
from roundup_crypto_lab.deployment_engine import (
    INTERVAL,
    deployment_buckets,
    deployment_dates,
)
from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan

DEFAULT_STRATEGY_REGISTRY = Path("config/dca-strategy-registry.json")

_BASELINE_ORDER = (
    "fixed_immediate",
    "fixed_daily",
    "fixed_weekly",
    "fixed_monthly",
)
_BASELINE_NAMES = {
    "fixed_immediate": ("BuyAndHold", "immediate"),
    "fixed_daily": ("DailyDCA", "daily_dca"),
    "fixed_weekly": ("WeeklyDCA", "weekly_dca"),
    "fixed_monthly": ("MonthlyDCA", "monthly_dca"),
}
_LEGACY_METHODS = {
    "immediate": "fixed_immediate",
    "daily_dca": "fixed_daily",
    "weekly_dca": "fixed_weekly",
    "monthly_dca": "fixed_monthly",
}


@dataclass(frozen=True)
class BaselineAllocation:
    """One exact gross amount assigned to one deterministic decision instant."""

    contributed_at: datetime
    scheduled_at: datetime
    gross_amount: Decimal


@dataclass(frozen=True)
class _FixedBaselineStrategy:
    strategy_id: str
    strategy_version: str
    gross_amount: Decimal
    implementation: str
    minimum_order: MinimumOrderRule

    def __post_init__(self) -> None:
        try:
            amount = Decimal(str(self.gross_amount))
            minimum = Decimal(self.minimum_order.amount)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("baseline order amounts must be decimals") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("baseline gross amount must be finite and positive")
        if not minimum.is_finite() or minimum < 0:
            raise ValueError("baseline minimum order must be finite and non-negative")
        object.__setattr__(self, "gross_amount", amount)

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        minimum = Decimal(self.minimum_order.amount)
        amount = self.gross_amount
        if amount < minimum:
            if self.minimum_order.behavior == "skip":
                return DcaDecision(
                    decision_tag=f"{self.implementation}.below-minimum",
                    diagnostics={"scheduled_gross_amount": amount},
                )
            amount = context.available_cash
            if amount < minimum:
                return DcaDecision(
                    decision_tag=f"{self.implementation}.insufficient-minimum",
                    diagnostics={"scheduled_gross_amount": self.gross_amount},
                )
        return DcaDecision(
            decision_tag=f"{self.implementation}.scheduled-buy",
            orders=(DcaBuyOrder(amount, "scheduled-allocation"),),
            diagnostics={"scheduled_gross_amount": self.gross_amount},
        )


@dataclass(frozen=True)
class BuyAndHold(_FixedBaselineStrategy):
    """Invest each newly available funding bucket immediately."""


@dataclass(frozen=True)
class DailyDCA(_FixedBaselineStrategy):
    """Invest one exact bucket slice on every scheduled UTC day."""


@dataclass(frozen=True)
class WeeklyDCA(_FixedBaselineStrategy):
    """Invest one exact bucket slice on the configured UTC weekday."""


@dataclass(frozen=True)
class MonthlyDCA(_FixedBaselineStrategy):
    """Invest one exact bucket slice on successive monthly anniversaries."""


_STRATEGY_CLASSES = {
    "fixed_immediate": BuyAndHold,
    "fixed_daily": DailyDCA,
    "fixed_weekly": WeeklyDCA,
    "fixed_monthly": MonthlyDCA,
}


def baseline_name(definition: StrategyDefinition) -> str:
    """Return the historical benchmark name for one registered baseline."""
    try:
        return _BASELINE_NAMES[definition.implementation][0]
    except KeyError as exc:
        raise ValueError(
            f"unsupported baseline implementation: {definition.implementation}"
        ) from exc


def deployment_method(definition: StrategyDefinition) -> str:
    """Return the historical deployment-method identifier for compatibility consumers."""
    try:
        return _BASELINE_NAMES[definition.implementation][1]
    except KeyError as exc:
        raise ValueError(
            f"unsupported baseline implementation: {definition.implementation}"
        ) from exc


def registered_baselines(
    registry: StrategyRegistry,
    *,
    include_immediate: bool,
    include_monthly: bool,
) -> tuple[StrategyDefinition, ...]:
    """Select each required baseline from the registry in historical output order."""
    required = ["fixed_daily", "fixed_weekly"]
    if include_immediate:
        required.insert(0, "fixed_immediate")
    if include_monthly:
        required.append("fixed_monthly")

    by_implementation: dict[str, StrategyDefinition] = {}
    for definition in registry.strategies:
        if definition.implementation not in _BASELINE_NAMES:
            continue
        if definition.implementation in by_implementation:
            raise ValueError(
                "registry contains multiple baseline strategies for "
                f"{definition.implementation}"
            )
        by_implementation[definition.implementation] = definition
    if include_immediate and "fixed_immediate" not in by_implementation:
        by_implementation["fixed_immediate"] = _compatibility_definition(
            "fixed_immediate", 0
        )
    missing = [
        implementation
        for implementation in required
        if implementation not in by_implementation
    ]
    if missing:
        raise ValueError(f"registry is missing baseline implementations: {', '.join(missing)}")
    return tuple(
        by_implementation[implementation]
        for implementation in _BASELINE_ORDER
        if implementation in required
    )


def effective_parameters(
    definition: StrategyDefinition,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, str | int]:
    """Apply explicit compatibility overrides without accepting unknown parameters."""
    parameters = dict(definition.parameters)
    for name, value in ({} if overrides is None else overrides).items():
        if name not in parameters:
            raise ValueError(f"unsupported parameter override for {definition.strategy_id}: {name}")
        parameters[name] = value
    if definition.implementation == "fixed_weekly":
        weekday = parameters.get("weekday")
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise ValueError("weekly baseline weekday must be an integer from 0 through 6")
    elif parameters:
        raise ValueError(f"{definition.implementation} does not accept parameters")
    return {name: parameters[name] for name in sorted(parameters)}


def _next_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _scheduled_dates(
    definition: StrategyDefinition,
    start: datetime,
    end: datetime,
    parameters: Mapping[str, str | int],
) -> list[datetime]:
    implementation = definition.implementation
    if implementation == "fixed_immediate":
        return [start]
    if implementation == "fixed_daily":
        return deployment_dates(start, end, "daily_dca", 0)
    if implementation == "fixed_weekly":
        return deployment_dates(start, end, "weekly_dca", int(parameters["weekday"]))
    if implementation == "fixed_monthly":
        scheduled = []
        current = start
        while current < end:
            scheduled.append(current)
            current = _next_month(current)
        return scheduled
    raise ValueError(f"unsupported baseline implementation: {implementation}")


def allocation_schedule(
    definition: StrategyDefinition,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    *,
    parameter_overrides: Mapping[str, Any] | None = None,
) -> tuple[BaselineAllocation, ...]:
    """Reproduce the historical exact bucket splitting for one registered baseline."""
    parameters = effective_parameters(definition, parameter_overrides)
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC) + INTERVAL
    allocations: list[BaselineAllocation] = []
    buckets = deployment_buckets(events)
    for position, bucket in enumerate(buckets):
        next_at = buckets[position + 1].contributed_at if position + 1 < len(buckets) else end
        scheduled = _scheduled_dates(definition, bucket.contributed_at, next_at, parameters)
        if not scheduled:
            continue
        portion = bucket.amount / len(scheduled)
        amounts = [portion] * (len(scheduled) - 1)
        amounts.append(bucket.amount - sum(amounts, Decimal("0")))
        allocations.extend(
            BaselineAllocation(bucket.contributed_at, scheduled_at, amount)
            for scheduled_at, amount in zip(scheduled, amounts, strict=True)
        )
    return tuple(allocations)


def _portfolio_state_at(
    purchases: list[dict[str, Any]],
    decision_at: datetime,
    candles: pd.DataFrame,
) -> tuple[Decimal, Decimal, Decimal]:
    quantity = Decimal("0")
    fees = Decimal("0")
    for purchase in purchases:
        if datetime.fromisoformat(purchase["executed_at"]) <= decision_at:
            quantity += purchase["quantity"]
            fees += purchase["fee_paid"]
    eligible = candles[candles["date"] + pd.Timedelta(INTERVAL) <= decision_at]
    mark = Decimal("0") if eligible.empty else Decimal(str(eligible.iloc[-1]["close"]))
    return quantity, fees, quantity * mark


def _strategy_for_allocation(
    definition: StrategyDefinition,
    gross_amount: Decimal,
) -> _FixedBaselineStrategy:
    strategy_class = _STRATEGY_CLASSES.get(definition.implementation)
    if strategy_class is None:
        raise ValueError(f"unsupported baseline implementation: {definition.implementation}")
    return strategy_class(
        strategy_id=definition.strategy_id,
        strategy_version=definition.strategy_version,
        gross_amount=gross_amount,
        implementation=definition.implementation,
        minimum_order=definition.minimum_order,
    )


def deploy_registered_baseline(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    definition: StrategyDefinition,
    *,
    parameter_overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a registered fixed baseline exclusively through the DCA strategy interface."""
    allocations = allocation_schedule(
        definition,
        events,
        candles,
        parameter_overrides=parameter_overrides,
    )
    purchases: list[dict[str, Any]] = []
    bucket_totals = {
        bucket.contributed_at: bucket.amount for bucket in deployment_buckets(events)
    }
    unreserved = dict(bucket_totals)
    remaining_allocations: dict[datetime, list[Decimal]] = {}
    for allocation in allocations:
        remaining_allocations.setdefault(allocation.contributed_at, []).append(
            allocation.gross_amount
        )

    for allocation in allocations:
        scheduled_amounts = remaining_allocations[allocation.contributed_at]
        if not scheduled_amounts or scheduled_amounts.pop(0) != allocation.gross_amount:
            raise RuntimeError("baseline allocation ordering invariant failed")
        current_remaining = allocation.gross_amount + sum(
            scheduled_amounts, Decimal("0")
        )
        unreserved[allocation.contributed_at] = current_remaining
        pending = tuple(
            PendingCashBucket(contributed_at, amount)
            for contributed_at, amount in sorted(unreserved.items())
            if contributed_at <= allocation.scheduled_at and amount > 0
        )
        quantity, fees, marked_asset_value = _portfolio_state_at(
            purchases,
            allocation.scheduled_at,
            candles,
        )
        context = build_decision_context(
            decision_at=allocation.scheduled_at,
            available_cash=sum((bucket.amount for bucket in pending), Decimal("0")),
            quantity=quantity,
            marked_asset_value=marked_asset_value,
            cumulative_contributions=sum(
                (
                    event.amount
                    for event in events
                    if event.contributed_at <= allocation.scheduled_at
                ),
                Decimal("0"),
            ),
            cumulative_fees=fees,
            pending_cash=pending,
            candles=candles,
        )
        strategy = _strategy_for_allocation(definition, allocation.gross_amount)
        decision = evaluate_strategy(strategy, context)
        requested = sum((order.gross_amount for order in decision.orders), Decimal("0"))
        if requested > current_remaining:
            raise ValueError("baseline strategy requested more than its scheduled funding bucket")
        funding_event = CashFlowEvent(
            allocation.contributed_at,
            bucket_totals[allocation.contributed_at],
            "deployment",
        )
        executions = execute_decision(
            candles=candles,
            funding_event=funding_event,
            context=context,
            decision=decision,
            fee_ratio=plan.fee_ratio,
        )
        for execution in executions:
            execution.pop("decision_tag", None)
            execution.pop("order_tag", None)
            purchases.append(execution)
        if requested == allocation.gross_amount:
            unreserved[allocation.contributed_at] = sum(
                scheduled_amounts, Decimal("0")
            )
        else:
            unreserved[allocation.contributed_at] = current_remaining - requested

    return purchases


def strategy_metadata(
    registry: StrategyRegistry,
    definition: StrategyDefinition,
    *,
    parameter_overrides: Mapping[str, Any] | None = None,
    repository_commit: str | None = None,
) -> dict[str, Any]:
    """Return deterministic registry identity and effective baseline parameters."""
    metadata: dict[str, Any] = {
        "registry_schema_version": registry.registry_schema_version,
        "registry_id": registry.registry_id,
        "registry_digest": registry.digest,
        "strategy_id": definition.strategy_id,
        "strategy_version": definition.strategy_version,
        "implementation": definition.implementation,
        "implementation_identity": definition.implementation_identity,
        "parameters": effective_parameters(definition, parameter_overrides),
    }
    if repository_commit is not None:
        metadata["repository_commit"] = repository_commit
    return metadata


def _compatibility_definition(implementation: str, weekly_day: int) -> StrategyDefinition:
    if implementation not in _BASELINE_NAMES:
        raise ValueError(f"unsupported baseline implementation: {implementation}")
    parameters: Mapping[str, str | int] = (
        {"weekday": weekly_day} if implementation == "fixed_weekly" else {}
    )
    benchmark, _ = _BASELINE_NAMES[implementation]
    return StrategyDefinition(
        strategy_id=benchmark.replace("DCA", "-dca").replace("And", "-and-").lower(),
        implementation=implementation,
        implementation_identity=f"roundup_crypto_lab.dca.{implementation}@1",
        strategy_version="1",
        hypothesis="Legacy compatibility projection through the causal strategy interface.",
        decision_cadence={
            "fixed_immediate": "funding_event",
            "fixed_daily": "daily",
            "fixed_weekly": "weekly",
            "fixed_monthly": "monthly",
        }[implementation],
        required_indicators=(),
        parameters=parameters,
        maximum_pending_cash_age_days=None,
        minimum_order=MinimumOrderRule("0", "skip"),
        research_status="baseline",
    )


def deploy_legacy_baseline(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    method: str,
    weekly_day: int,
) -> list[dict[str, Any]]:
    """Compatibility adapter for the removed hard-coded deployment switch."""
    try:
        implementation = _LEGACY_METHODS[method]
    except KeyError as exc:
        raise ValueError(f"unsupported passive deployment method: {method}") from exc
    definition = _compatibility_definition(implementation, weekly_day)
    return deploy_registered_baseline(plan, events, candles, definition)
