from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from roundup_crypto_lab.execution_costs import (
    ExecutionCostProfile,
    execute_costed_purchase,
)
from roundup_crypto_lab.investment_plan import CashFlowEvent


def test_zero_spread_is_exactly_zero_when_decimal_round_trip_overshoots() -> None:
    at = datetime(2024, 1, 1, tzinfo=UTC)
    price = Decimal("10153.75")
    gross = Decimal("40")

    # This reproduces the finite-precision round trip that made the old
    # implementation derive a tiny negative spread cost for a zero-spread profile.
    assert (gross / price) * price > gross

    candles = pd.DataFrame(
        {
            "date": pd.to_datetime([at], utc=True),
            "open": [price],
            "high": [price],
            "low": [price],
            "close": [price],
            "volume": [Decimal("1")],
        }
    )
    profile = ExecutionCostProfile(
        cost_profile_id="rounding-control-v1",
        profile_version=1,
        description="Zero-cost regression profile.",
        profile_kind="control",
        trading_fee_ratio="0",
        half_spread_ratio="0",
        fixed_order_fee="0",
        minimum_order_amount="0",
        below_minimum_behavior="carry_forward",
    )

    execution = execute_costed_purchase(
        candles,
        CashFlowEvent(at, gross, "regression"),
        at,
        gross,
        profile,
    )

    assert execution is not None
    assert execution["execution_price"] == price
    assert execution["estimated_spread_cost"] == Decimal("0")
    assert execution["fee_paid"] == Decimal("0")
