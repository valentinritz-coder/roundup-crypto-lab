"""Auditable cash-flow adapter for active, single-position backtests.

Freqtrade's public backtesting interface accepts one starting wallet; it does not
provide a public historical ``deposit`` event.  This module therefore does not
monkey-patch Freqtrade.  Instead it is a small repository-owned execution
    adapter: a strategy supplies decisions derived from already-completed candles,
    and this adapter applies the shared :class:`InvestmentPlan`, wallet
rules, and spot fees.  It is deliberately single-asset and single-position.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan, contribution_schedule


class CapitalMode(StrEnum):
    """Funding modes supported by the active-backtest adapter."""

    ONE_SHOT_CAPITAL = "one_shot_capital"
    RECURRING_MONTHLY_CONTRIBUTIONS = "recurring_monthly_contributions"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class Candle:
    """The prices available at one strategy decision point (all values are exact)."""

    timestamp: datetime
    open: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("candle timestamp must be timezone-aware")
        if self.open <= 0 or self.close <= 0:
            raise ValueError("candle prices must be positive")


@dataclass(frozen=True)
class StrategyDecision:
    """A normal strategy decision, never an investor cash-flow instruction.

    ``stake`` is a gross EUR order amount for a buy.  For a sell it must be
    omitted: this lab closes the sole open spot position in full.
    """

    action: Action = Action.HOLD
    stake: Decimal | None = None


@dataclass(frozen=True)
class WalletState:
    timestamp: datetime
    cash: Decimal
    position_quantity: Decimal
    open_position: bool


# The provider deliberately receives no candle.  Signals must be prepared from
# completed data before execution; this makes observing a candle close before an
# order at that candle's open impossible through this public contract.
DecisionProvider = Callable[[WalletState], StrategyDecision]


def _maximum_drawdown(values: Iterable[Decimal]) -> Decimal:
    peak: Decimal | None = None
    drawdown = Decimal("0")
    for value in values:
        if peak is None or value > peak:
            peak = value
        elif peak and peak > 0:
            drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def _events(
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
    mode: CapitalMode,
) -> tuple[CashFlowEvent, ...]:
    events = contribution_schedule(plan, start, end)
    if mode is CapitalMode.ONE_SHOT_CAPITAL:
        return tuple(event for event in events if event.kind == "initial")
    return events


def run_active_backtest(
    candles: Iterable[Candle],
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
    decide: DecisionProvider,
    *,
    mode: CapitalMode = CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS,
) -> dict[str, object]:
    """Run deterministic spot accounting around causal strategy decisions.

    Contributions are applied before the decision at the first candle whose
    timestamp is at or after their UTC timestamp.  Decisions are precomputed
    from the *previous completed* candle; the provider gets only wallet state,
    never current OHLCV. No callback is made for a contribution, so a deposit
    cannot create a trade. A buy exceeding available cash fails closed.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("timerange timestamps must be timezone-aware")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if start >= end:
        raise ValueError("timerange start date must be strictly before end date")
    ordered = tuple(candles)
    if not ordered:
        raise ValueError("active backtest needs at least one candle")
    if any(
        candle.timestamp.astimezone(UTC) < start or candle.timestamp.astimezone(UTC) >= end
        for candle in ordered
    ):
        raise ValueError("candles must be within the end-exclusive timerange")
    if tuple(sorted(ordered, key=lambda candle: candle.timestamp)) != ordered:
        raise ValueError("candles must be chronologically ordered")

    events = _events(plan, start, end, mode)
    event_index = 0
    cash = quantity = contributed = current_deployed = cumulative_deployed = fees = Decimal("0")
    shares = Decimal("0")
    share_value = Decimal("1")
    contributions: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []

    for candle in ordered:
        timestamp = candle.timestamp.astimezone(UTC)
        while event_index < len(events) and events[event_index].contributed_at <= timestamp:
            event = events[event_index]
            equity_before = cash + quantity * candle.open
            share_value = Decimal("1") if shares == 0 else equity_before / shares
            before = cash
            cash += event.amount
            contributed += event.amount
            shares += event.amount / share_value
            contributions.append(
                {
                    "investor_contribution_timestamp": event.contributed_at.isoformat(),
                    "credited_at": timestamp.isoformat(),
                    "kind": event.kind,
                    "amount": event.amount,
                    "wallet_cash_before": before,
                    "wallet_cash_after": cash,
                    "total_contributed_capital": contributed,
                }
            )
            event_index += 1

        state = WalletState(timestamp, cash, quantity, quantity > 0)
        decision = decide(state)
        if not isinstance(decision, StrategyDecision):
            raise ValueError("strategy decision provider must return StrategyDecision")
        if decision.action is Action.BUY:
            if quantity:
                raise ValueError("maximum one open position")
            if decision.stake is None or decision.stake <= 0 or decision.stake > cash:
                raise ValueError("buy stake must be positive and no greater than available cash")
            gross = decision.stake
            fee = gross * plan.fee_ratio
            quantity = (gross - fee) / candle.open
            cash -= gross
            current_deployed = gross
            cumulative_deployed += gross
            fees += fee
            trades.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "side": "buy",
                    "gross_stake": gross,
                    "price": candle.open,
                    "fee_paid": fee,
                    "quantity": quantity,
                }
            )
        elif decision.action is Action.SELL:
            if not quantity:
                raise ValueError("cannot sell without an open position")
            gross = quantity * candle.open
            fee = gross * plan.fee_ratio
            cash += gross - fee
            fees += fee
            trades.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "side": "sell",
                    "gross_stake": gross,
                    "price": candle.open,
                    "fee_paid": fee,
                    "quantity": quantity,
                }
            )
            quantity = Decimal("0")
            current_deployed = Decimal("0")
        elif decision.action is not Action.HOLD:
            raise ValueError("unknown strategy action")

        crypto_value = quantity * candle.close
        equity = cash + crypto_value
        if shares:
            share_value = equity / shares
        equity_curve.append(
            {
                "timestamp": timestamp.isoformat(),
                "free_cash": cash,
                "current_deployed_capital": current_deployed,
                "cumulative_gross_deployed": cumulative_deployed,
                "crypto_value": crypto_value,
                "equity": equity,
                "cumulative_contributions": contributed,
                "investment_return": equity - contributed,
                "time_weighted_share_value": share_value,
            }
        )
    if event_index != len(events):
        raise ValueError("timerange candles did not credit every contribution")
    final_equity = equity_curve[-1]["equity"]
    share_values = (row["time_weighted_share_value"] for row in equity_curve)
    return {
        "capital_mode": mode.value,
        "contribution_schedule": [
            {
                "contributed_at": event.contributed_at.isoformat(),
                "amount": event.amount,
                "kind": event.kind,
            }
            for event in events
        ],
        "contribution_ledger": contributions,
        "trades": trades,
        "equity_curve": equity_curve,
        "total_contributed_capital": contributed,
        "current_deployed_capital": current_deployed,
        "cumulative_gross_deployed": cumulative_deployed,
        "free_cash": cash,
        "final_equity": final_equity,
        "investment_return": final_equity - contributed,
        "fees_paid": fees,
        "contribution_neutral_return": share_value - Decimal("1"),
        "contribution_neutral_max_drawdown": _maximum_drawdown(share_values),
    }
