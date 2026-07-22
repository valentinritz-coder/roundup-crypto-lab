"""Causal, single-position spot execution with separate investor cash flows.

At each candle, eligible contributions are credited first.  Stops known before the
candle are then tested against its OHLC range (open gap first, then intrabar low),
before an already-scheduled signal exit is allowed at the open.  The close is
used only for the final mark-to-market snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan, contribution_schedule


class CapitalMode(StrEnum):
    ONE_SHOT_CAPITAL = "one_shot_capital"
    RECURRING_MONTHLY_CONTRIBUTIONS = "recurring_monthly_contributions"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class Candle:
    """One OHLC snapshot. ``atr`` was computed from the prior completed candle."""

    timestamp: datetime
    open: Decimal
    close: Decimal
    high: Decimal | None = None
    low: Decimal | None = None
    atr: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("candle timestamp must be timezone-aware")
        high, low = self.high or self.open, self.low or self.open
        if min(self.open, self.close, high, low) <= 0 or high < low:
            raise ValueError("candle prices must be positive with high >= low")
        if self.atr is not None and self.atr <= 0:
            raise ValueError("ATR must be positive")


@dataclass(frozen=True)
class LifecycleSettings:
    """Supported effective Freqtrade lifecycle settings for this spot adapter."""

    fixed_stoploss: Decimal
    use_custom_stoploss: bool = False
    atr_stop_multiplier: Decimal | None = None
    use_exit_signal: bool = True

    def __post_init__(self) -> None:
        if not Decimal("-1") < self.fixed_stoploss < 0:
            raise ValueError("fixed stoploss must be in (-1, 0)")
        if self.use_custom_stoploss and (
            self.atr_stop_multiplier is None or self.atr_stop_multiplier <= 0
        ):
            raise ValueError("custom stoploss requires a positive ATR multiplier")


@dataclass(frozen=True)
class StrategyDecision:
    action: Action = Action.HOLD
    stake: Decimal | None = None
    exit_tag: str | None = None


@dataclass(frozen=True)
class WalletState:
    timestamp: datetime
    cash: Decimal
    position_quantity: Decimal
    open_position: bool


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
    plan: InvestmentPlan, start: datetime, end: datetime, mode: CapitalMode
) -> tuple[CashFlowEvent, ...]:
    events = contribution_schedule(plan, start, end)
    return tuple(
        event
        for event in events
        if mode is not CapitalMode.ONE_SHOT_CAPITAL or event.kind == "initial"
    )


def run_active_backtest(
    candles: Iterable[Candle],
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
    decide: DecisionProvider,
    *,
    mode: CapitalMode = CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS,
    lifecycle: LifecycleSettings | None = None,
) -> dict[str, object]:
    """Execute causal decisions; an end-open position remains marked at final close.

    A stop has deterministic priority over a signal exit in the same candle. A
    gap below the stop fills at open; otherwise a low touching it fills at the
    stop. ATR stops are raised from prior-completed-candle ATR only and never
    lowered. Fees apply to both entry and exit notional.
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
        c.timestamp.astimezone(UTC) < start or c.timestamp.astimezone(UTC) >= end for c in ordered
    ):
        raise ValueError("candles must be within the end-exclusive timerange")
    if tuple(sorted(ordered, key=lambda c: c.timestamp)) != ordered:
        raise ValueError("candles must be chronologically ordered")
    lifecycle = lifecycle or LifecycleSettings(Decimal("-0.12"))
    events, event_index = _events(plan, start, end, mode), 0
    cash = quantity = contributed = current_deployed = cumulative_deployed = fees = Decimal("0")
    shares, share_value = Decimal("0"), Decimal("1")
    contributions: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []
    open_trade: dict[str, object] | None = None
    stop_price: Decimal | None = None
    trade_number = 0

    def close_trade(candle: Candle, price: Decimal, reason: str, tag: str | None = None) -> None:
        nonlocal cash, quantity, fees, current_deployed, open_trade, stop_price
        assert open_trade is not None
        gross = quantity * price
        fee = gross * plan.fee_ratio
        cash += gross - fee
        fees += fee
        open_trade.update(
            {
                "exit_timestamp": candle.timestamp.astimezone(UTC).isoformat(),
                "exit_price": price,
                "exit_fee": fee,
                "exit_reason": reason,
                "exit_tag": tag,
                "net_proceeds": gross - fee,
                "total_fees": open_trade["entry_fee"] + fee,
            }
        )
        quantity = Decimal("0")
        current_deployed = Decimal("0")
        open_trade = None
        stop_price = None

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
        # ATR was exposed only from a completed predecessor candle by the bridge.
        if quantity and lifecycle.use_custom_stoploss and candle.atr is not None:
            candidate = candle.open - lifecycle.atr_stop_multiplier * candle.atr  # type: ignore[operator]
            stop_price = max(stop_price or candidate, candidate)
        decision = decide(WalletState(timestamp, cash, quantity, quantity > 0))
        if not isinstance(decision, StrategyDecision):
            raise ValueError("strategy decision provider must return StrategyDecision")
        low = candle.low or candle.open
        if quantity and stop_price is not None and (candle.open <= stop_price or low <= stop_price):
            close_trade(
                candle, candle.open if candle.open <= stop_price else stop_price, "stop_loss"
            )
        elif decision.action is Action.SELL:
            if not quantity:
                raise ValueError("cannot sell without an open position")
            if lifecycle.use_exit_signal:
                close_trade(candle, candle.open, "exit_signal", decision.exit_tag)
        elif decision.action is Action.BUY:
            if quantity:
                raise ValueError("maximum one open position")
            if decision.stake is None or decision.stake <= 0:
                raise ValueError("buy stake must be positive")
            gross = decision.stake
            fee = gross * plan.fee_ratio
            if gross + fee > cash:
                raise ValueError("buy stake plus fee must not exceed available cash")
            # Freqtrade's exported stake excludes the entry fee; its filled
            # amount is therefore stake / entry price while the wallet pays
            # stake plus fee.
            quantity = gross / candle.open
            cash -= gross + fee
            fees += fee
            current_deployed = gross
            cumulative_deployed += gross
            trade_number += 1
            stop_price = candle.open * (Decimal("1") + lifecycle.fixed_stoploss)
            open_trade = {
                "trade_id": f"trade-{trade_number:06d}",
                "timestamp": timestamp.isoformat(),
                "entry_timestamp": timestamp.isoformat(),
                "entry_price": candle.open,
                "entry_gross_stake": gross,
                "entry_fee": fee,
                "quantity": quantity,
                "initial_stop_price": stop_price,
                "exit_timestamp": None,
                "exit_price": None,
                "exit_fee": None,
                "exit_reason": None,
                "exit_tag": None,
            }
            trades.append(open_trade)
            # Native backtesting can fill a newly opened position's stop from
            # the entry candle's remaining intrabar range.
            if low <= stop_price:
                close_trade(
                    candle, candle.open if candle.open <= stop_price else stop_price, "stop_loss"
                )
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
                "open_stop_price": stop_price,
            }
        )
    if event_index != len(events):
        raise ValueError("timerange candles did not credit every contribution")
    final_equity = equity_curve[-1]["equity"]
    share_values = (row["time_weighted_share_value"] for row in equity_curve)
    return {
        "capital_mode": mode.value,
        "contribution_schedule": [
            {"contributed_at": e.contributed_at.isoformat(), "amount": e.amount, "kind": e.kind}
            for e in events
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
        "end_of_range_position": "open_marked_at_final_close" if quantity else "closed",
    }
