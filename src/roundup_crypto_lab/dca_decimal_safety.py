"""Residual-safe decimal helpers for controlled DCA campaign execution."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from roundup_crypto_lab.deployment_engine import purchase as deployment_purchase
from roundup_crypto_lab.investment_plan import CashFlowEvent

ACCOUNTING_EPSILON = Decimal("1e-24")


def canonical_decimal(value: Decimal) -> str:
    """Serialize a finite Decimal without stripping significant integer zeroes."""
    if not value.is_finite():
        raise ValueError("numeric values must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def normalize_residual(value: Decimal) -> Decimal:
    """Canonicalize only sub-quantum accounting residue to exact zero."""
    if not value.is_finite():
        raise ValueError("accounting balances must be finite")
    return Decimal("0") if abs(value) <= ACCOUNTING_EPSILON else value


def exact_purchase(
    candles: pd.DataFrame,
    event: CashFlowEvent,
    scheduled_at: datetime,
    amount: Decimal,
    fee: Decimal,
) -> dict[str, Any] | None:
    """Build a purchase whose gross amount is exactly fee plus net contribution."""
    executed = deployment_purchase(candles, event, scheduled_at, amount, fee)
    if executed is None:
        return None
    gross = executed["gross_contribution"]
    fee_paid = executed["fee_paid"]
    net = gross - fee_paid
    executed["net_contribution"] = net
    executed["quantity"] = net / executed["execution_price"]
    return executed


def consume_fifo(
    pending: list[list[Any]],
    amount: Decimal,
) -> list[dict[str, str]]:
    """Consume pending contribution buckets while tolerating sub-quantum residue."""
    if not amount.is_finite() or amount < 0:
        raise ValueError("pilot execution amount must be finite and non-negative")

    available = sum((Decimal(str(bucket[1])) for bucket in pending), Decimal("0"))
    if amount - available > ACCOUNTING_EPSILON:
        raise ValueError(
            "pilot execution exceeds available contributed cash "
            f"by {canonical_decimal(amount - available)}"
        )

    remaining = amount
    consumed: list[dict[str, str]] = []
    for bucket in pending:
        if remaining <= ACCOUNTING_EPSILON:
            remaining = Decimal("0")
            break
        balance = Decimal(str(bucket[1]))
        if balance <= 0:
            continue
        take = min(balance, remaining)
        bucket[1] = normalize_residual(balance - take)
        remaining = normalize_residual(remaining - take)
        if take > 0:
            contributed_at = bucket[0]
            if not isinstance(contributed_at, datetime):
                raise TypeError("pending cash timestamps must be datetimes")
            consumed.append(
                {
                    "contributed_at": contributed_at.isoformat(),
                    "amount": canonical_decimal(take),
                }
            )

    if remaining > ACCOUNTING_EPSILON:
        raise ValueError(
            "pilot execution exceeds available contributed cash "
            f"by {canonical_decimal(remaining)}"
        )

    pending[:] = [
        bucket
        for bucket in pending
        if normalize_residual(Decimal(str(bucket[1]))) > 0
    ]
    return consumed
