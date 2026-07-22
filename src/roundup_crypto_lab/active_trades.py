"""Validate single-position trade ledgers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from roundup_crypto_lab.active_common import EXITS, _mapping, _nonnegative, _positive, ts


def _validate_trades(
    trade_values: list[object], start: datetime, end: datetime
) -> tuple[Decimal, Decimal, dict[str, int], list[dict[str, Any]]]:
    fees = Decimal()
    deployed = Decimal()
    exits: dict[str, int] = {}
    trades: list[dict[str, Any]] = []
    previous_exit = start
    saw_open = False
    for index, trade_value in enumerate(trade_values, start=1):
        trade = _mapping(trade_value, f"trade {index}")
        if saw_open:
            raise ValueError("an open trade must be the final ledger row")
        entry = ts(trade.get("entry_timestamp"), "entry timestamp")
        if not start <= entry < end or entry < previous_exit:
            raise ValueError("trade entries overlap or are out of range")
        entry_price = _positive(trade.get("entry_price"), "entry price")
        stake = _positive(trade.get("entry_gross_stake"), "entry stake")
        quantity = _positive(trade.get("quantity"), "quantity")
        cash_available = _nonnegative(trade.get("cash_available"), "cash available")
        entry_fee = _nonnegative(trade.get("entry_fee"), "entry fee")
        if stake + entry_fee > cash_available:
            raise ValueError("buy exceeds cash")
        fees += entry_fee
        reason = trade.get("exit_reason")
        if reason is None:
            if index != len(trade_values):
                raise ValueError("an open trade must be the final ledger row")
            for field in ("exit_timestamp", "exit_price", "exit_fee", "net_proceeds"):
                if trade.get(field) is not None:
                    raise ValueError("open trade has exit fields")
            if trade.get("total_fees") not in (None, entry_fee, str(entry_fee)):
                raise ValueError("open trade total fees are inconsistent")
            deployed = stake
            saw_open = True
        else:
            if reason not in EXITS:
                raise ValueError("unsupported exit")
            required = ("exit_timestamp", "exit_price", "exit_fee", "net_proceeds", "total_fees")
            if any(trade.get(field) is None for field in required):
                raise ValueError("closed trade missing fields")
            exit_at = ts(trade.get("exit_timestamp"), "exit timestamp")
            if not entry <= exit_at < end:
                raise ValueError("invalid exit timestamp")
            exit_price = _positive(trade.get("exit_price"), "exit price")
            exit_fee = _nonnegative(trade.get("exit_fee"), "exit fee")
            total_fees = _nonnegative(trade.get("total_fees"), "total fees")
            net_proceeds = _nonnegative(trade.get("net_proceeds"), "net proceeds")
            if total_fees != entry_fee + exit_fee:
                raise ValueError("inconsistent fees")
            gross_proceeds = quantity * exit_price
            if abs(net_proceeds - (gross_proceeds - exit_fee)) > Decimal("1e-18"):
                raise ValueError("net proceeds are inconsistent")
            fees += exit_fee
            exits[str(reason)] = exits.get(str(reason), 0) + 1
            previous_exit = exit_at
        trade["_entry_at"] = entry
        trade["_entry_price"] = entry_price
        trade["_stake"] = stake
        trade["_quantity"] = quantity
        trade["_exit_at"] = (
            None if reason is None else ts(trade.get("exit_timestamp"), "exit timestamp")
        )
        trades.append(trade)
    return fees, deployed, exits, trades
