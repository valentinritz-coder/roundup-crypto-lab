"""Freqtrade-backtesting-compatible stop execution for the active bridge."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    DecisionProvider,
    LifecycleSettings,
    StrategyDecision,
    WalletState,
    _events,
    _maximum_drawdown,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan


def run_freqtrade_compatible_backtest(
    candles: Iterable[Candle],
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
    decide: DecisionProvider,
    *,
    mode: CapitalMode = CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS,
    lifecycle: LifecycleSettings | None = None,
) -> dict[str, object]:
    """Execute the adapter using Freqtrade's backtesting custom-stop convention.

    For long trades Freqtrade supplies the current candle high as ``current_rate``
    to ``custom_stoploss`` and evaluates the resulting stop against that candle's
    low. The current analyzed candle is visible through the data provider. The
    initial fixed stop remains the lower bound and every later stop can only rise.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("timerange timestamps must be timezone-aware")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if start >= end:
        raise ValueError("timerange start date must be strictly before end date")
    ordered = tuple(candles)
    if not ordered:
        raise ValueError("active backtest needs at least one candle")
    if any(not start <= candle.timestamp.astimezone(UTC) < end for candle in ordered):
        raise ValueError("candles must be within the end-exclusive timerange")
    if tuple(sorted(ordered, key=lambda candle: candle.timestamp)) != ordered:
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

    def update_custom_stop(candle: Candle) -> None:
        nonlocal stop_price
        if (
            not quantity
            or not lifecycle.use_custom_stoploss
            or candle.atr is None
            or open_trade is None
        ):
            return
        current_rate = candle.high or candle.open
        candidate = current_rate - lifecycle.atr_stop_multiplier * candle.atr  # type: ignore[operator]
        previous = stop_price
        stop_price = max(stop_price or candidate, candidate)
        updates = open_trade.setdefault("stop_updates", [])
        assert isinstance(updates, list)
        updates.append(
            {
                "timestamp": candle.timestamp.astimezone(UTC).isoformat(),
                "current_rate": current_rate,
                "atr": candle.atr,
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

        update_custom_stop(candle)
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
            update_custom_stop(candle)
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
            {"contributed_at": event.contributed_at.isoformat(), "amount": event.amount, "kind": event.kind}
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
        "end_of_range_position": "open_marked_at_final_close" if quantity else "closed",
    }
