"""Validate active equity curves against contributions and positions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from roundup_crypto_lab.active_common import _mapping, _nonnegative, _positive, dec, ts


def _validate_curve(
    curve_values: list[object],
    trades: list[dict[str, Any]],
    credited_events: list[tuple[datetime, Decimal]],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], Decimal, Decimal]:
    previous_time: datetime | None = None
    previous_contributions = Decimal()
    previous_gross = Decimal()
    shares: list[Decimal] = []
    last: dict[str, Any] | None = None
    for index, row_value in enumerate(curve_values, start=1):
        row = _mapping(row_value, f"equity row {index}")
        timestamp = ts(row.get("timestamp"), "equity timestamp")
        if not start <= timestamp < end or (
            previous_time is not None and timestamp <= previous_time
        ):
            raise ValueError("equity curve is not strictly chronological")
        mark_price = _positive(row.get("mark_price"), "mark price")
        free_cash = _nonnegative(row.get("free_cash"), "free cash")
        crypto_value = _nonnegative(row.get("crypto_value"), "crypto value")
        current_deployed = _nonnegative(
            row.get("current_deployed_capital"), "current deployed capital"
        )
        cumulative_gross = _nonnegative(
            row.get("cumulative_gross_deployed"), "cumulative gross deployed"
        )
        equity = dec(row.get("equity"), "equity")
        cumulative_contributions = _nonnegative(
            row.get("cumulative_contributions"), "cumulative contributions"
        )
        investment_return = dec(row.get("investment_return"), "investment return")
        share_value = _positive(row.get("time_weighted_share_value"), "share value")
        if equity != free_cash + crypto_value:
            raise ValueError("equity row identity failed")
        if investment_return != equity - cumulative_contributions:
            raise ValueError("investment-return row identity failed")
        expected_contributions = sum(
            (amount for credited_at, amount in credited_events if credited_at <= timestamp),
            Decimal(),
        )
        if cumulative_contributions != expected_contributions:
            raise ValueError("curve contribution total differs from ledger")
        entered = [trade for trade in trades if trade["_entry_at"] <= timestamp]
        expected_gross = sum((trade["_stake"] for trade in entered), Decimal())
        if cumulative_gross != expected_gross:
            raise ValueError("curve gross deployment differs from trade ledger")
        open_at_timestamp = [
            trade for trade in entered if trade["_exit_at"] is None or trade["_exit_at"] > timestamp
        ]
        if len(open_at_timestamp) > 1:
            raise ValueError("equity curve implies overlapping positions")
        if open_at_timestamp:
            open_trade = open_at_timestamp[0]
            if current_deployed != open_trade["_stake"]:
                raise ValueError("curve deployed capital differs from open trade")
            if crypto_value != open_trade["_quantity"] * mark_price:
                raise ValueError(curve crypto value differs from open quantity)
        elif current_deployed != 0 or crypto_value != 0:
            raise ValueError("closed curve row retains deployed capital or crypto")
        if cumulative_contributions < previous_contributions or cumulative_gross < previous_gross:
            raise ValueError("cumulative curve fields decreased")
        previous_time = timestamp
        previous_contributions = cumulative_contributions
        previous_gross = cumulative_gross
        shares.append(share_value)
        last = row
    if last is None:
        raise ValueError("equity curve is empty")
    peak = shares[0]
    drawdown = Decimal()
    for share in shares:
        peak = max(peak, share)
        drawdown = max(drawdown, (peak - share) / peak)
    if not Decimal() <= drawdown <= Decimal(1):
        raise ValueError("invalid contribution-neutral drawdown")
    return last, shares[-1] - Decimal(1), drawdown
