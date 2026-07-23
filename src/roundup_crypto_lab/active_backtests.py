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
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
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
    """One OHLC snapshot with the ATR visible to Freqtrade for this candle."""

    timestamp: datetime
    open: Decimal
    close: Decimal
    high: Decimal | None = None
    low: Decimal | None = None
    atr: Decimal | None = None
    after_fill_atr: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("candle timestamp must be timezone-aware")
        high, low = self.high or self.open, self.low or self.open
        if min(self.open, self.close, high, low) <= 0 or high < low:
            raise ValueError("candle prices must be positive with high >= low")
        if self.atr is not None and self.atr <= 0:
            raise ValueError("ATR must be positive")
        if self.after_fill_atr is not None and self.after_fill_atr <= 0:
            raise ValueError("after-fill ATR must be positive")


@dataclass(frozen=True)
class LifecycleSettings:
    """Supported effective Freqtrade lifecycle settings for this spot adapter."""

    fixed_stoploss: Decimal
    use_custom_stoploss: bool = False
    atr_stop_multiplier: Decimal | None = None
    use_exit_signal: bool = True
    price_tick: Decimal = Decimal("0.00000001")
    amount_step: Decimal = Decimal("0.00000001")

    def __post_init__(self) -> None:
        if not Decimal("-1") < self.fixed_stoploss < 0:
            raise ValueError("fixed stoploss must be in (-1, 0)")
        if self.use_custom_stoploss and (
            self.atr_stop_multiplier is None or self.atr_stop_multiplier <= 0
        ):
            raise ValueError("custom stoploss requires a positive ATR multiplier")
        if self.price_tick <= 0 or self.amount_step <= 0:
            raise ValueError("execution precision steps must be positive")


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


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


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
    stop. On the entry candle, Freqtrade first invokes ``custom_stoploss`` with
    ``after_fill=True`` and the entry price as ``current_rate``. If that stop is
    not crossed, it then invokes the normal callback on the same candle using its
    high. Stops can only tighten. Fees apply to both entry and exit notional.
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

    def update_custom_stop(candle: Candle, *, after_fill: bool = False) -> None:
        nonlocal stop_price
        atr = (candle.after_fill_atr or candle.atr) if after_fill else candle.atr
        if (
            not quantity
            or not lifecycle.use_custom_stoploss
            or atr is None
            or open_trade is None
        ):
            return
        current_rate = candle.open if after_fill else (candle.high or candle.open)
        raw_candidate = current_rate - lifecycle.atr_stop_multiplier * atr  # type: ignore[operator]
        candidate = _round_up(raw_candidate, lifecycle.price_tick)
        previous = stop_price
        stop_price = max(stop_price or candidate, candidate)
        updates = open_trade.setdefault("stop_updates", [])
        assert isinstance(updates, list)
        updates.append(
            {
                "timestamp": candle.timestamp.astimezone(UTC).isoformat(),
                "current_rate": current_rate,
                "atr": atr,
                "after_fill": after_fill,
                "candidate_stop_price": candidate,
                "stop_price_before": previous,
                "stop_price_after": stop_price,
            }
        )

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
        decision = decide(WalletState(timestamp, cash, quantity, quantity > 0))
        if not isinstance(decision, StrategyDecision):
            raise ValueError("strategy decision provider must return StrategyDecision")
        low = candle.low or candle.open
        if quantity and stop_price is not None and (candle.open <= stop_price or low <= stop_price):
            close_trade(
                candle, candle.open if candle.open <= stop_price else stop_price, "stop_loss"
            )
        elif quantity:
            previous_stop = stop_price
            update_custom_stop(candle)
            if stop_price is not None and stop_price != previous_stop and low <= stop_price:
                close_trade(candle, stop_price, "stop_loss")
            elif decision.action is Action.SELL and lifecycle.use_exit_signal:
                close_trade(candle, candle.open, "exit_signal", decision.exit_tag)
            elif decision.action is Action.BUY:
                raise ValueError("maximum one open position")
        elif decision.action is Action.SELL:
            raise ValueError("cannot sell without an open position")
        elif decision.action is Action.BUY:
            if quantity:
                raise ValueError("maximum one open position")
            if decision.stake is None or decision.stake <= 0:
                raise ValueError("buy stake must be positive")
            requested_gross = decision.stake
            quantity = _round_down(requested_gross / candle.open, lifecycle.amount_step)
            if quantity <= 0:
                raise ValueError("buy amount rounds to zero at exchange precision")
            gross = quantity * candle.open
            fee = gross * plan.fee_ratio
            if gross + fee > cash:
                raise ValueError("buy stake plus fee must not exceed available cash")
            # Freqtrade truncates the filled base amount to exchange precision,
            # then derives the effective stake from amount times entry price.
            cash -= gross + fee
            fees += fee
            current_deployed = gross
            cumulative_deployed += gross
            trade_number += 1
            stop_price = _round_up(
                candle.open * (Decimal("1") + lifecycle.fixed_stoploss), lifecycle.price_tick
            )
            open_trade = {
                "trade_id": f"trade-{trade_number:06d}",
                "timestamp": timestamp.isoformat(),
                "entry_timestamp": timestamp.isoformat(),
                "entry_price": candle.open,
                "entry_gross_stake": gross,
                "cash_available": cash + gross + fee,
                "entry_fee": fee,
                "quantity": quantity,
                "initial_stop_price": stop_price,
                "stop_updates": [],
                "exit_timestamp": None,
                "exit_price": None,
                "exit_fee": None,
                "exit_reason": None,
                "exit_tag": None,
            }
            trades.append(open_trade)
            # Native Freqtrade performs two sequential callbacks on an entry candle.
            # The immediate after-fill callback uses the entry rate. If that stop
            # survives the candle low, the regular callback then uses the candle high.
            update_custom_stop(candle, after_fill=True)
            assert stop_price is not None
            if low <= stop_price:
                close_trade(
                    candle, candle.open if candle.open <= stop_price else stop_price, "stop_loss"
                )
            else:
                previous_stop = stop_price
                update_custom_stop(candle)
                if stop_price is not None and stop_price != previous_stop and low <= stop_price:
                    close_trade(candle, stop_price, "stop_loss")
        elif decision.action is not Action.HOLD:
            raise ValueError("unknown strategy action")
        crypto_value = quantity * candle.close
        equity = cash + crypto_value
        if shares:
            share_value = equity / shares
        equity_curve.append(
            {
                "timestamp": timestamp.isoformat(),
                "mark_price": candle.close,
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
