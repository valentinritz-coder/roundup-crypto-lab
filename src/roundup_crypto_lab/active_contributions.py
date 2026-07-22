"""Validate investor contribution schedules and ledgers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from roundup_crypto_lab.active_common import _mapping, _nonnegative, _positive, dec, ts


def _validate_contributions(
    schedule_values: list[object],
    ledger_values: list[object],
    start: datetime,
    end: datetime,
) -> tuple[Decimal, list[tuple[datetime, Decimal]]]:
    if len(schedule_values) != len(ledger_values):
        raise ValueError("schedule/ledger length differs")
    total = Decimal()
    credited_events: list[tuple[datetime, Decimal]] = []
    previous_schedule = previous_investor = previous_credit = start
    for index, (scheduled_value, credited_value) in enumerate(
        zip(schedule_values, ledger_values, strict=True), start=1
    ):
        scheduled = _mapping(scheduled_value, f"schedule row {index}")
        credited = _mapping(credited_value, f"contribution row {index}")
        contributed_at = ts(scheduled.get("contributed_at"), "contributed_at")
        investor_at = ts(
            credited.get("investor_contribution_timestamp"),
            "investor_contribution_timestamp",
        )
        credited_at = ts(credited.get("credited_at"), "credited_at")
        if not start <= contributed_at < end or contributed_at < previous_schedule:
            raise ValueError("contribution schedule is not chronological")
        if not start <= investor_at < end or investor_at < previous_investor:
            raise ValueError("investor contribution ledger is not chronological")
        if not investor_at <= credited_at < end or credited_at < previous_credit:
            raise ValueError("credited contribution timestamp is invalid")
        schedule_amount = _positive(scheduled.get("amount"), "scheduled amount")
        ledger_amount = _positive(credited.get("amount"), "credited amount")
        if (
            contributed_at != investor_at
            or schedule_amount != ledger_amount
            or scheduled.get("kind") != credited.get("kind")
            or scheduled.get("kind") not in {"initial", "monthly"}
        ):
            raise ValueError("contribution ledger differs from schedule")
        before = _nonnegative(credited.get("wallet_cash_before"), "wallet cash before")
        after = _nonnegative(credited.get("wallet_cash_after"), "wallet cash after")
        if after != before + ledger_amount:
            raise ValueError("contribution cash reconciliation failed")
        total += ledger_amount
        if dec(credited.get("total_contributed_capital"), "contribution cumulative total") != total:
            raise ValueError("contribution cumulative total differs")
        credited_events.append((credited_at, ledger_amount))
        previous_schedule = contributed_at
        previous_investor = investor_at
        previous_credit = credited_at
    return total, credited_events
