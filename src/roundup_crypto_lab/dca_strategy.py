"""Causal, deterministic contracts for buy-only DCA deployment strategies."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from roundup_crypto_lab.deployment_engine import INTERVAL, purchase
from roundup_crypto_lab.investment_plan import CashFlowEvent

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_REQUIRED_CANDLE_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _decimal(
    value: Decimal | str | int,
    name: str,
    *,
    allow_zero: bool = True,
) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")
    return result


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{name} must match {_IDENTIFIER.pattern} for stable machine-readable artifacts"
        )
    return value


def _freeze_json(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{name} keys must be strings")
        frozen: dict[str, Any] = {}
        for key in sorted(value):
            frozen[key] = _freeze_json(value[key], f"{name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{name}[]") for item in value)
    if isinstance(value, datetime):
        return _utc(value, name)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} Decimal values must be finite")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError(
        f"{name} must contain only mappings, sequences, strings, integers, booleans, "
        "null, finite Decimal values or timezone-aware datetimes"
    )


def _artifact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _artifact_value(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_artifact_value(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


@dataclass(frozen=True)
class CompletedCandle:
    """OHLCV values from a candle that closed no later than the decision instant."""

    opened_at: datetime
    closed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        opened_at = _utc(self.opened_at, "candle opened_at")
        closed_at = _utc(self.closed_at, "candle closed_at")
        if closed_at <= opened_at:
            raise ValueError("candle closed_at must be after opened_at")
        object.__setattr__(self, "opened_at", opened_at)
        object.__setattr__(self, "closed_at", closed_at)
        for name in ("open", "high", "low", "close"):
            object.__setattr__(
                self,
                name,
                _decimal(getattr(self, name), f"candle {name}", allow_zero=False),
            )
        object.__setattr__(self, "volume", _decimal(self.volume, "candle volume"))
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("candle high must not be below open, low or close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("candle low must not be above open, high or close")


@dataclass(frozen=True)
class PendingCashBucket:
    """Already-contributed cash that remains available to a strategy."""

    contributed_at: datetime
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "contributed_at", _utc(self.contributed_at, "contributed_at"))
        object.__setattr__(
            self,
            "amount",
            _decimal(self.amount, "pending cash amount", allow_zero=False),
        )

    def age_at(self, decision_at: datetime) -> timedelta:
        decision_at = _utc(decision_at, "decision_at")
        if self.contributed_at > decision_at:
            raise ValueError("future cash is not visible to a DCA strategy")
        return decision_at - self.contributed_at


@dataclass(frozen=True)
class CausalIndicator:
    """A registered indicator value known no later than its observation timestamp."""

    name: str
    value: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "indicator name"))
        try:
            value = Decimal(str(self.value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("indicator value must be a decimal number") from exc
        if not value.is_finite():
            raise ValueError("indicator value must be finite")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "indicator observed_at"))


@dataclass(frozen=True)
class DcaDecisionContext:
    """Immutable portfolio and prior-known market data visible to one decision."""

    decision_at: datetime
    available_cash: Decimal
    quantity: Decimal
    marked_asset_value: Decimal
    cumulative_contributions: Decimal
    cumulative_fees: Decimal
    pending_cash: tuple[PendingCashBucket, ...] = ()
    prior_candles: tuple[CompletedCandle, ...] = ()
    indicators: tuple[CausalIndicator, ...] = ()
    state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        decision_at = _utc(self.decision_at, "decision_at")
        object.__setattr__(self, "decision_at", decision_at)
        for name in (
            "available_cash",
            "quantity",
            "marked_asset_value",
            "cumulative_contributions",
            "cumulative_fees",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))

        pending_cash = tuple(self.pending_cash)
        if any(not isinstance(bucket, PendingCashBucket) for bucket in pending_cash):
            raise TypeError("pending cash entries must be PendingCashBucket values")
        if tuple(sorted(pending_cash, key=lambda bucket: bucket.contributed_at)) != pending_cash:
            raise ValueError("pending cash buckets must be chronological")
        if any(bucket.contributed_at > decision_at for bucket in pending_cash):
            raise ValueError("future contribution events must be invisible")
        if sum((bucket.amount for bucket in pending_cash), Decimal("0")) > self.available_cash:
            raise ValueError("pending cash buckets exceed available cash")
        object.__setattr__(self, "pending_cash", pending_cash)

        prior_candles = tuple(self.prior_candles)
        if any(not isinstance(candle, CompletedCandle) for candle in prior_candles):
            raise TypeError("prior candle entries must be CompletedCandle values")
        opened = [candle.opened_at for candle in prior_candles]
        if opened != sorted(opened) or len(set(opened)) != len(opened):
            raise ValueError("prior candles must be chronological and unique")
        if any(candle.closed_at > decision_at for candle in prior_candles):
            raise ValueError("current or future candle values are not causal")
        object.__setattr__(self, "prior_candles", prior_candles)

        indicators = tuple(self.indicators)
        if any(not isinstance(indicator, CausalIndicator) for indicator in indicators):
            raise TypeError("indicator entries must be CausalIndicator values")
        names = [indicator.name for indicator in indicators]
        if names != sorted(names) or len(set(names)) != len(names):
            raise ValueError("indicators must be uniquely named and sorted")
        if any(indicator.observed_at > decision_at for indicator in indicators):
            raise ValueError("future indicator values are not causal")
        object.__setattr__(self, "indicators", indicators)
        object.__setattr__(self, "state", _freeze_json(self.state, "strategy state"))


@dataclass(frozen=True)
class DcaBuyOrder:
    """A buy-only request expressed as an exact gross cash amount."""

    gross_amount: Decimal
    order_tag: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "gross_amount",
            _decimal(self.gross_amount, "gross amount", allow_zero=False),
        )
        object.__setattr__(self, "order_tag", _identifier(self.order_tag, "order tag"))


@dataclass(frozen=True)
class DcaDecision:
    """Auditable strategy output for one decision timestamp."""

    decision_tag: str
    orders: tuple[DcaBuyOrder, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    next_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_tag", _identifier(self.decision_tag, "decision tag"))
        orders = tuple(self.orders)
        if any(not isinstance(order, DcaBuyOrder) for order in orders):
            raise TypeError("orders must contain DcaBuyOrder values")
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "diagnostics", _freeze_json(self.diagnostics, "diagnostics"))
        object.__setattr__(self, "next_state", _freeze_json(self.next_state, "next state"))


@runtime_checkable
class DcaStrategy(Protocol):
    """Deterministic buy-only strategy contract."""

    strategy_id: str
    strategy_version: str

    def decide(self, context: DcaDecisionContext) -> DcaDecision:
        """Return a decision without mutating portfolio accounting state."""


def build_decision_context(
    *,
    decision_at: datetime,
    available_cash: Decimal | str,
    quantity: Decimal | str,
    marked_asset_value: Decimal | str,
    cumulative_contributions: Decimal | str,
    cumulative_fees: Decimal | str,
    pending_cash: tuple[PendingCashBucket, ...],
    candles: pd.DataFrame,
    indicators: tuple[CausalIndicator, ...] = (),
    state: Mapping[str, Any] | None = None,
    candle_interval: timedelta = INTERVAL,
) -> DcaDecisionContext:
    """Build a context containing only candles completed by ``decision_at``."""
    decision_at = _utc(decision_at, "decision_at")
    if not isinstance(candle_interval, timedelta) or candle_interval <= timedelta(0):
        raise ValueError("candle interval must be positive")
    if any(column not in candles for column in _REQUIRED_CANDLE_COLUMNS):
        raise ValueError("candles must contain date, open, high, low, close and volume")
    frame = candles.loc[:, _REQUIRED_CANDLE_COLUMNS].copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    if not frame["date"].is_monotonic_increasing or frame["date"].duplicated().any():
        raise ValueError("candle timestamps must be monotonic and unique")

    prior_candles = []
    for row in frame.itertuples(index=False):
        opened_at = row.date.to_pydatetime()
        closed_at = opened_at + candle_interval
        if closed_at > decision_at:
            continue
        prior_candles.append(
            CompletedCandle(
                opened_at=opened_at,
                closed_at=closed_at,
                open=Decimal(str(row.open)),
                high=Decimal(str(row.high)),
                low=Decimal(str(row.low)),
                close=Decimal(str(row.close)),
                volume=Decimal(str(row.volume)),
            )
        )
    return DcaDecisionContext(
        decision_at=decision_at,
        available_cash=Decimal(str(available_cash)),
        quantity=Decimal(str(quantity)),
        marked_asset_value=Decimal(str(marked_asset_value)),
        cumulative_contributions=Decimal(str(cumulative_contributions)),
        cumulative_fees=Decimal(str(cumulative_fees)),
        pending_cash=pending_cash,
        prior_candles=tuple(prior_candles),
        indicators=indicators,
        state={} if state is None else state,
    )


def validate_decision(context: DcaDecisionContext, decision: DcaDecision) -> DcaDecision:
    """Fail closed if a strategy requests invalid or unavailable cash."""
    if not isinstance(context, DcaDecisionContext):
        raise TypeError("context must be a DcaDecisionContext")
    if not isinstance(decision, DcaDecision):
        raise TypeError("strategy must return a DcaDecision")
    tags = [order.order_tag for order in decision.orders]
    if len(set(tags)) != len(tags):
        raise ValueError("order tags must be unique within a decision")
    requested = sum((order.gross_amount for order in decision.orders), Decimal("0"))
    if requested > context.available_cash:
        raise ValueError("DCA decision exceeds currently available cash")
    return decision


def evaluate_strategy(strategy: DcaStrategy, context: DcaDecisionContext) -> DcaDecision:
    """Evaluate and validate one strategy decision."""
    _identifier(strategy.strategy_id, "strategy id")
    _identifier(strategy.strategy_version, "strategy version")
    return validate_decision(context, strategy.decide(context))


def context_artifact(context: DcaDecisionContext) -> dict[str, Any]:
    """Return a canonical machine-readable representation of strategy inputs."""
    return {
        "decision_at": context.decision_at.isoformat(),
        "available_cash": str(context.available_cash),
        "quantity": str(context.quantity),
        "marked_asset_value": str(context.marked_asset_value),
        "cumulative_contributions": str(context.cumulative_contributions),
        "cumulative_fees": str(context.cumulative_fees),
        "pending_cash": [
            {
                "contributed_at": bucket.contributed_at.isoformat(),
                "amount": str(bucket.amount),
                "age_seconds": int(bucket.age_at(context.decision_at).total_seconds()),
            }
            for bucket in context.pending_cash
        ],
        "prior_candles": [
            {
                "opened_at": candle.opened_at.isoformat(),
                "closed_at": candle.closed_at.isoformat(),
                "open": str(candle.open),
                "high": str(candle.high),
                "low": str(candle.low),
                "close": str(candle.close),
                "volume": str(candle.volume),
            }
            for candle in context.prior_candles
        ],
        "indicators": [
            {
                "name": indicator.name,
                "value": str(indicator.value),
                "observed_at": indicator.observed_at.isoformat(),
            }
            for indicator in context.indicators
        ],
        "state": _artifact_value(context.state),
    }


def decision_artifact(
    strategy: DcaStrategy,
    context: DcaDecisionContext,
    decision: DcaDecision,
) -> dict[str, Any]:
    """Return the canonical audit record for a validated decision."""
    decision = validate_decision(context, decision)
    return {
        "strategy_id": _identifier(strategy.strategy_id, "strategy id"),
        "strategy_version": _identifier(strategy.strategy_version, "strategy version"),
        "context": context_artifact(context),
        "decision": {
            "decision_tag": decision.decision_tag,
            "orders": [
                {"gross_amount": str(order.gross_amount), "order_tag": order.order_tag}
                for order in decision.orders
            ],
            "diagnostics": _artifact_value(decision.diagnostics),
            "next_state": _artifact_value(decision.next_state),
        },
    }


def decision_artifact_bytes(
    strategy: DcaStrategy,
    context: DcaDecisionContext,
    decision: DcaDecision,
) -> bytes:
    """Serialize a decision with stable keys, separators and exact Decimal strings."""
    return json.dumps(
        decision_artifact(strategy, context, decision),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def execute_decision(
    *,
    candles: pd.DataFrame,
    funding_event: CashFlowEvent,
    context: DcaDecisionContext,
    decision: DcaDecision,
    fee_ratio: Decimal | str,
) -> list[dict[str, Any]]:
    """Validate every order before using the deployment engine's first-candle rule."""
    decision = validate_decision(context, decision)
    fee = _decimal(fee_ratio, "fee ratio")
    if fee >= 1:
        raise ValueError("fee ratio must be lower than 1")
    contributed_at = _utc(funding_event.contributed_at, "funding contributed_at")
    if contributed_at > context.decision_at:
        raise ValueError("a strategy cannot spend future contributions")

    executions = []
    for order in decision.orders:
        executed = purchase(
            candles,
            funding_event,
            context.decision_at,
            order.gross_amount,
            fee,
        )
        if executed is not None:
            executed["decision_tag"] = decision.decision_tag
            executed["order_tag"] = order.order_tag
            executions.append(executed)
    return executions
