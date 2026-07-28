from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.dca_decimal_safety import (
    ACCOUNTING_EPSILON,
    canonical_decimal,
    consume_fifo,
    normalize_residual,
)


def test_canonical_decimal_preserves_integer_zeroes() -> None:
    assert canonical_decimal(Decimal("1000")) == "1000"
    assert canonical_decimal(Decimal("40")) == "40"
    assert canonical_decimal(Decimal("1.2300")) == "1.23"
    assert canonical_decimal(Decimal("0.000")) == "0"


def test_fifo_normalizes_only_sub_quantum_residue() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    pending = [
        [at, Decimal("0.3333333333333333333333333333")],
        [at, Decimal("0.6666666666666666666666666666")],
    ]

    allocations = consume_fifo(pending, Decimal("1"))
    allocated = sum((Decimal(row["amount"]) for row in allocations), Decimal("0"))

    assert pending == []
    assert abs(Decimal("1") - allocated) <= ACCOUNTING_EPSILON
    assert normalize_residual(ACCOUNTING_EPSILON) == Decimal("0")


def test_fifo_rejects_material_overspend() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    pending = [[at, Decimal("1")]]

    with pytest.raises(
        ValueError,
        match=r"exceeds available contributed cash by 0\.01",
    ):
        consume_fifo(pending, Decimal("1.01"))


def test_fifo_rejects_non_finite_amount() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        consume_fifo([], Decimal("NaN"))
