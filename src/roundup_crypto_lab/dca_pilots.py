"""Preregistered, buy-only pilot DCA strategies for causal deployment research."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from roundup_crypto_lab.dca_registry import (
    MinimumOrderRule,
    StrategyDefinition,
    StrategyRegistry,
)
from roundup_crypto_lab.dca_strategy import (
    DcaBuyOrder,
    DcaDecision,
    DcaDecisionContext,
)

PILOT_IMPLEMENTATIONS = frozenset(
    {
        "immediate_floor_drawdown_reserve",
        "no_sell_value_averaging",
        "moving_average_deviation",
        "ker_adx_accumulation",
    }
)
_PILOT_ORDER = (
    "immediate_floor_drawdown_reserve",
    "no_sell_value_averaging",
    "moving_average_deviation",
    "ker_adx_accumulation",
)


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _parameter(definition: StrategyDefinition, name: str) -> Decimal:
    try:
        value = definition.parameters[name]
    except KeyError as exc:
        raise ValueError(f"{definition.strategy_id} is missing parameter {name}") from exc
    return _decimal(value, f"{definition.strategy_id}.{name}")


def _indicator(context: DcaDecisionContext, name: str) -> Decimal:
    for indicator in context.indicators:
        if indicator.name == name:
            return indicator.value
    raise ValueError(f"missing required causal indicator: {name}")


def _state_decimal(context: DcaDecisionContext, name: str, default: str = "0") -> Decimal:
    return _decimal(context.state.get(name, default), f"strategy state {name}")


def _state_integer(context: DcaDecisionContext, name: str, default: int = 0) -> int:
    value = context.state.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"strategy state {name} must be an integer")
    if value < 0:
        raise ValueError(f"strategy state {name} must be non-negative")
    return value


def _new_contribution(context: DcaDecisionContext) -> Decimal:
    processed = _state_decimal(context, "processed_contributions")
    if processed > context.cumulative_contributions:
        raise ValueError("strategy state exceeds visible cumulative contributions")
    return context.cumulative_contributions - processed


def _next_state(context: DcaDecisionContext, **updates: Any) -> dict[str, Any]:
    state = dict(context.state)
    state.update(updates)
    state["decision_count"] = _state_integer(context, "decision_count") + 1
    return state


def _expired(context: DcaDecisionContext, maximum_age_days: int) -> bool:
    if maximum_age_days < 0:
        raise ValueError("maximum pending cash age must be non-negative")
    limit = timedelta(days=maximum_age_days)
    return any(bucket.age_at(context.decision_at) >= limit for bucket in context.pending_cash)


def _minimum_rule(definition: StrategyDefinition) -> MinimumOrderRule:
    rule = definition.minimum_order
    minimum = _decimal(rule.amount, "minimum order")
    if minimum < 0:
        raise ValueError("minimum order must be non-negative")
    return rule


def _decision(
    *,
    definition: StrategyDefinition,
    context: DcaDecisionContext,
    decision_tag: str,
    amount: Decimal,
    diagnostics: Mapping[str, Any],
    next_state: Mapping[str, Any],
) -> DcaDecision:
    amount = min(max(amount, Decimal("0")), context.available_cash)
    rule = _minimum_rule(definition)
    minimum = Decimal(rule.amount)
    if amount == 0:
        return DcaDecision(
            decision_tag=decision_tag,
            diagnostics=diagnostics,
            next_state=next_state,
        )
    if amount < minimum:
        if rule.behavior == "spend_available":
            amount = context.available_cash
        if amount < minimum:
            return DcaDecision(
                decision_tag=f"{definition.implementation}.skip-below-minimum",
                diagnostics={
                    **dict(diagnostics),
                    "requested_gross_amount": amount,
                    "minimum_order": minimum,
                },
                next_state=next_state,
            )
    return DcaDecision(
        decision_tag=decision_tag,
        orders=(DcaBuyOrder(amount, "pilot-allocation"),),
        diagnostics={
            **dict(diagnostics),
            "requested_gross_amount": amount,
        },
        next_state=next_state,
    )


@dataclass(frozen=True)
class ImmediateFloorDrawdownReserve:
    """Deploy a contribution floor and release reserve through causal drawdown tiers."""

    definition: StrategyDefinition

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.definition.strategy_version

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        drawdown = _indicator(context, "rolling_drawdown")
        if not Decimal("0") <= drawdown <= Decimal("1"):
            raise ValueError("rolling_drawdown must be between zero and one")

        new_contribution = _new_contribution(context)
        previous_tier = _state_integer(context, "released_drawdown_tier")
        floor_fraction = _parameter(self.definition, "immediate_floor_fraction")
        thresholds = (
            _parameter(self.definition, "tier_1_drawdown"),
            _parameter(self.definition, "tier_2_drawdown"),
            _parameter(self.definition, "tier_3_drawdown"),
        )
        release_fractions = (
            Decimal("0"),
            _parameter(self.definition, "tier_1_release_fraction"),
            _parameter(self.definition, "tier_2_release_fraction"),
            _parameter(self.definition, "tier_3_release_fraction"),
        )
        current_tier = sum(drawdown >= threshold for threshold in thresholds)
        if previous_tier > 3:
            raise ValueError("strategy state drawdown_tier must be between zero and three")

        next_state = _next_state(
            context,
            processed_contributions=context.cumulative_contributions,
            released_drawdown_tier=max(previous_tier, current_tier),
        )
        if _expired(context, self._maximum_age_days()):
            return _decision(
                definition=self.definition,
                context=context,
                decision_tag="immediate_floor_drawdown_reserve.buy-cash-expiry",
                amount=context.available_cash,
                diagnostics={
                    "rolling_drawdown": drawdown,
                    "drawdown_tier": current_tier,
                    "cash_expired": True,
                },
                next_state=next_state,
            )

        new_floor = min(new_contribution, context.available_cash) * floor_fraction
        new_reserve = max(
            Decimal("0"), min(new_contribution, context.available_cash) - new_floor
        )
        new_release = new_reserve * release_fractions[current_tier]

        existing_reserve = max(
            Decimal("0"),
            context.available_cash - min(new_contribution, context.available_cash),
        )
        previous_release = release_fractions[min(previous_tier, 3)]
        current_release = release_fractions[current_tier]
        incremental_release = Decimal("0")
        if current_release > previous_release and previous_release < 1:
            incremental_release = (
                existing_reserve
                * (current_release - previous_release)
                / (Decimal("1") - previous_release)
            )

        amount = new_floor + new_release + incremental_release
        if current_tier > previous_tier:
            tag = f"immediate_floor_drawdown_reserve.buy-tier-{current_tier}"
        elif amount > 0:
            tag = "immediate_floor_drawdown_reserve.buy-floor"
        else:
            tag = "immediate_floor_drawdown_reserve.skip-reserve"

        return _decision(
            definition=self.definition,
            context=context,
            decision_tag=tag,
            amount=amount,
            diagnostics={
                "rolling_drawdown": drawdown,
                "previous_released_drawdown_tier": previous_tier,
                "drawdown_tier": current_tier,
                "new_contribution": new_contribution,
                "new_floor": new_floor,
                "new_release": new_release,
                "incremental_release": incremental_release,
                "cash_expired": False,
            },
            next_state=next_state,
        )

    def _maximum_age_days(self) -> int:
        value = self.definition.maximum_pending_cash_age_days
        if value is None:
            raise ValueError("drawdown reserve requires maximum_pending_cash_age_days")
        return value


@dataclass(frozen=True)
class NoSellValueAveraging:
    """Buy only the value-path shortfall and never sell when the portfolio is ahead."""

    definition: StrategyDefinition

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.definition.strategy_version

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        target_multiplier = _parameter(self.definition, "target_value_multiplier")
        floor_fraction = _parameter(
            self.definition, "minimum_new_contribution_fraction"
        )
        new_contribution = _new_contribution(context)
        target_value = context.cumulative_contributions * target_multiplier
        shortfall = max(Decimal("0"), target_value - context.marked_asset_value)
        floor = min(new_contribution, context.available_cash) * floor_fraction
        amount = max(shortfall, floor)
        next_state = _next_state(
            context,
            processed_contributions=context.cumulative_contributions,
            last_target_value=target_value,
        )

        if _expired(context, self._maximum_age_days()):
            tag = "no_sell_value_averaging.buy-cash-expiry"
            amount = context.available_cash
        elif shortfall > 0:
            tag = "no_sell_value_averaging.buy-shortfall"
        elif floor > 0:
            tag = "no_sell_value_averaging.buy-minimum-floor"
        else:
            tag = "no_sell_value_averaging.skip-above-target"

        return _decision(
            definition=self.definition,
            context=context,
            decision_tag=tag,
            amount=amount,
            diagnostics={
                "target_value": target_value,
                "marked_asset_value": context.marked_asset_value,
                "shortfall": shortfall,
                "new_contribution": new_contribution,
                "minimum_floor": floor,
            },
            next_state=next_state,
        )

    def _maximum_age_days(self) -> int:
        value = self.definition.maximum_pending_cash_age_days
        if value is None:
            raise ValueError("value averaging requires maximum_pending_cash_age_days")
        return value


@dataclass(frozen=True)
class MovingAverageDeviationDca:
    """Scale a bounded cash allocation from the prior close's long-MA deviation."""

    definition: StrategyDefinition

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.definition.strategy_version

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        previous_close = _indicator(context, "previous_close")
        long_ma = _indicator(context, "long_ma")
        if previous_close <= 0 or long_ma <= 0:
            raise ValueError("previous_close and long_ma must be positive")

        deviation = (previous_close - long_ma) / long_ma
        below_threshold = _parameter(self.definition, "below_ma_threshold")
        above_threshold = _parameter(self.definition, "above_ma_threshold")
        base_fraction = _parameter(self.definition, "base_allocation_fraction")
        if deviation <= -below_threshold:
            regime = "below-ma"
            multiplier = _parameter(self.definition, "below_ma_multiplier")
        elif deviation >= above_threshold:
            regime = "above-ma"
            multiplier = _parameter(self.definition, "above_ma_multiplier")
        else:
            regime = "neutral"
            multiplier = _parameter(self.definition, "neutral_multiplier")

        allocation_fraction = min(Decimal("1"), base_fraction * multiplier)
        amount = context.available_cash * allocation_fraction
        next_state = _next_state(
            context,
            last_regime=regime,
            last_deviation=deviation,
        )
        if _expired(context, self._maximum_age_days()):
            tag = "moving_average_deviation.buy-cash-expiry"
            amount = context.available_cash
        elif amount > 0:
            tag = f"moving_average_deviation.buy-{regime}"
        else:
            tag = "moving_average_deviation.skip-zero-allocation"

        return _decision(
            definition=self.definition,
            context=context,
            decision_tag=tag,
            amount=amount,
            diagnostics={
                "previous_close": previous_close,
                "long_ma": long_ma,
                "deviation": deviation,
                "regime": regime,
                "multiplier": multiplier,
                "allocation_fraction": allocation_fraction,
            },
            next_state=next_state,
        )

    def _maximum_age_days(self) -> int:
        value = self.definition.maximum_pending_cash_age_days
        if value is None:
            raise ValueError("MA deviation requires maximum_pending_cash_age_days")
        return value


@dataclass(frozen=True)
class KerAdxAccumulation:
    """Use KER20 and ADX14 only to accelerate buy-only reserve deployment."""

    definition: StrategyDefinition

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.definition.strategy_version

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        ker = _indicator(context, "ker_20")
        adx = _indicator(context, "adx_14")
        if not Decimal("0") <= ker <= Decimal("1"):
            raise ValueError("ker_20 must be between zero and one")
        if adx < 0:
            raise ValueError("adx_14 must be non-negative")

        new_contribution = _new_contribution(context)
        floor_fraction = _parameter(self.definition, "immediate_floor_fraction")
        accelerated_fraction = _parameter(
            self.definition, "accelerated_release_fraction"
        )
        ker_threshold = _parameter(self.definition, "ker_threshold")
        adx_threshold = _parameter(self.definition, "adx_threshold")
        signal = ker >= ker_threshold and adx >= adx_threshold

        visible_new = min(new_contribution, context.available_cash)
        floor = visible_new * floor_fraction
        reserve = max(Decimal("0"), context.available_cash - floor)
        accelerated = reserve * accelerated_fraction if signal else Decimal("0")
        amount = floor + accelerated
        signal_count = _state_integer(context, "signal_count") + int(signal)
        next_state = _next_state(
            context,
            processed_contributions=context.cumulative_contributions,
            signal_count=signal_count,
            last_signal=signal,
        )

        if _expired(context, self._maximum_age_days()):
            tag = "ker_adx_accumulation.buy-cash-expiry"
            amount = context.available_cash
        elif signal:
            tag = "ker_adx_accumulation.buy-accelerated"
        elif floor > 0:
            tag = "ker_adx_accumulation.buy-immediate-floor"
        else:
            tag = "ker_adx_accumulation.skip-wait-signal"

        return _decision(
            definition=self.definition,
            context=context,
            decision_tag=tag,
            amount=amount,
            diagnostics={
                "ker_20": ker,
                "adx_14": adx,
                "ker_threshold": ker_threshold,
                "adx_threshold": adx_threshold,
                "signal": signal,
                "new_contribution": new_contribution,
                "immediate_floor": floor,
                "accelerated_release": accelerated,
            },
            next_state=next_state,
        )

    def _maximum_age_days(self) -> int:
        value = self.definition.maximum_pending_cash_age_days
        if value is None:
            raise ValueError("KER/ADX accumulation requires maximum_pending_cash_age_days")
        return value


_PILOT_CLASSES = {
    "immediate_floor_drawdown_reserve": ImmediateFloorDrawdownReserve,
    "no_sell_value_averaging": NoSellValueAveraging,
    "moving_average_deviation": MovingAverageDeviationDca,
    "ker_adx_accumulation": KerAdxAccumulation,
}


def build_pilot_strategy(
    definition: StrategyDefinition,
) -> (
    ImmediateFloorDrawdownReserve
    | NoSellValueAveraging
    | MovingAverageDeviationDca
    | KerAdxAccumulation
):
    """Instantiate one preregistered pilot from a validated registry definition."""
    if not isinstance(definition, StrategyDefinition):
        raise TypeError("definition must be a StrategyDefinition")
    strategy_class = _PILOT_CLASSES.get(definition.implementation)
    if strategy_class is None:
        raise ValueError(f"unsupported pilot implementation: {definition.implementation}")
    if definition.research_status != "preregistered":
        raise ValueError("pilot strategies must be preregistered before execution")
    if definition.maximum_pending_cash_age_days is None:
        raise ValueError("pilot strategies require a maximum pending cash age")
    return strategy_class(definition)


def registered_pilots(registry: StrategyRegistry) -> tuple[StrategyDefinition, ...]:
    """Return all four pilot definitions in their frozen research order."""
    if not isinstance(registry, StrategyRegistry):
        raise TypeError("registry must be a StrategyRegistry")
    by_implementation: dict[str, StrategyDefinition] = {}
    for definition in registry.strategies:
        if definition.implementation not in PILOT_IMPLEMENTATIONS:
            continue
        if definition.implementation in by_implementation:
            raise ValueError(
                "registry contains multiple pilot strategies for "
                f"{definition.implementation}"
            )
        by_implementation[definition.implementation] = definition
    missing = [
        implementation
        for implementation in _PILOT_ORDER
        if implementation not in by_implementation
    ]
    if missing:
        raise ValueError(f"registry is missing pilot implementations: {', '.join(missing)}")
    return tuple(by_implementation[implementation] for implementation in _PILOT_ORDER)
