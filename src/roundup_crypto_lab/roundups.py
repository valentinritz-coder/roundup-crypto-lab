"""Exact-cent roundup calculations for simple bank CSV exports."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

_CENT = Decimal("0.01")


class RoundupInputError(ValueError):
    """Raised when a transaction row cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class RoundupRecord:
    transaction_id: str
    booking_date: date
    description: str
    amount_cents: int
    roundup_cents: int


def decimal_euros_to_cents(value: str) -> int:
    """Parse an exact euro amount and return signed integer cents."""
    normalized = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(normalized).quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise RoundupInputError(f"invalid monetary amount: {value!r}") from exc
    return int(amount * 100)


def calculate_roundup_cents(amount_cents: int) -> int:
    """Return cents needed to round an outgoing payment to the next euro."""
    if amount_cents >= 0:
        return 0
    spent_cents = -amount_cents
    remainder = spent_cents % 100
    return 0 if remainder == 0 else 100 - remainder


def _stable_transaction_id(booking_date: date, description: str, amount_cents: int) -> str:
    payload = f"{booking_date.isoformat()}|{description.strip()}|{amount_cents}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def load_roundups(path: str | Path) -> list[RoundupRecord]:
    """Load date, description, amount and optional transaction_id from CSV."""
    records: list[RoundupRecord] = []
    seen_ids: set[str] = set()

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "description", "amount"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise RoundupInputError(f"missing CSV columns: {', '.join(sorted(missing))}")

        for line_number, row in enumerate(reader, start=2):
            try:
                booking_date = date.fromisoformat((row.get("date") or "").strip())
            except ValueError as exc:
                raise RoundupInputError(
                    f"line {line_number}: date must use YYYY-MM-DD"
                ) from exc

            description = (row.get("description") or "").strip()
            if not description:
                raise RoundupInputError(f"line {line_number}: empty description")

            amount_cents = decimal_euros_to_cents(row.get("amount") or "")
            supplied_id = (row.get("transaction_id") or "").strip()
            transaction_id = supplied_id or _stable_transaction_id(
                booking_date, description, amount_cents
            )
            if transaction_id in seen_ids:
                raise RoundupInputError(
                    f"line {line_number}: duplicate transaction_id {transaction_id!r}"
                )
            seen_ids.add(transaction_id)

            records.append(
                RoundupRecord(
                    transaction_id=transaction_id,
                    booking_date=booking_date,
                    description=description,
                    amount_cents=amount_cents,
                    roundup_cents=calculate_roundup_cents(amount_cents),
                )
            )

    return records
