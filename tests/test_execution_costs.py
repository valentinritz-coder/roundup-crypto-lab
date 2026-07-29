from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from roundup_crypto_lab.dca_decimal_safety import exact_purchase
from roundup_crypto_lab.execution_costs import (
    ExecutionCostProfile,
    execute_costed_purchase,
    execution_cost_summary,
    legacy_fee_profile,
    load_cost_profile,
    loads_cost_profile,
    resolve_cost_profile,
)
from roundup_crypto_lab.investment_plan import CashFlowEvent


def _candles() -> pd.DataFrame:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    return pd.DataFrame(
        {
            "date": pd.to_datetime([at], utc=True),
            "open": [Decimal("100")],
            "high": [Decimal("101")],
            "low": [Decimal("99")],
            "close": [Decimal("100")],
            "volume": [Decimal("1")],
        }
    )


def _profile(**overrides) -> ExecutionCostProfile:
    values = {
        "cost_profile_id": "test-profile-v1",
        "profile_version": 1,
        "description": "Deterministic test assumptions.",
        "profile_kind": "sensitivity",
        "trading_fee_ratio": "0.01",
        "half_spread_ratio": "0.02",
        "fixed_order_fee": "0.50",
        "minimum_order_amount": "1",
        "below_minimum_behavior": "carry_forward",
    }
    values.update(overrides)
    return ExecutionCostProfile(**values)


def test_costed_purchase_separates_explicit_fees_and_spread() -> None:
    candles = _candles()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    event = CashFlowEvent(at, Decimal("100"), "test")
    profile = _profile()
    execution = execute_costed_purchase(
        candles,
        event,
        at,
        Decimal("100"),
        profile,
    )
    assert execution is not None
    assert execution["reference_price"] == Decimal("100")
    assert execution["execution_price"] == Decimal("102")
    assert execution["trading_fee_paid"] == Decimal("1.00")
    assert execution["fixed_order_fee_paid"] == Decimal("0.50")
    assert execution["fee_paid"] == Decimal("1.50")
    assert execution["net_notional"] == Decimal("98.50")
    assert execution["gross_contribution"] == (
        execution["fee_paid"] + execution["net_contribution"]
    )
    assert execution["quantity"] == (
        execution["net_notional"] / execution["execution_price"]
    )
    assert execution["estimated_spread_cost"] == (
        execution["net_notional"]
        - execution["quantity"] * execution["reference_price"]
    )


def test_legacy_fee_profile_is_economically_identical() -> None:
    candles = _candles()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    event = CashFlowEvent(at, Decimal("40"), "test")
    legacy = exact_purchase(
        candles,
        event,
        at,
        Decimal("40"),
        Decimal("0.0026"),
    )
    costed = execute_costed_purchase(
        candles,
        event,
        at,
        Decimal("40"),
        legacy_fee_profile("0.0026"),
    )
    assert legacy is not None and costed is not None
    for field in (
        "gross_contribution",
        "fee_paid",
        "net_contribution",
        "quantity",
        "execution_price",
    ):
        assert costed[field] == legacy[field]
    assert costed["estimated_spread_cost"] == 0
    assert costed["fixed_order_fee_paid"] == 0


def test_fixed_fee_is_per_order_not_proportional() -> None:
    candles = _candles()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    profile = _profile()
    first = execute_costed_purchase(
        candles,
        CashFlowEvent(at, Decimal("10"), "test"),
        at,
        Decimal("10"),
        profile,
    )
    second = execute_costed_purchase(
        candles,
        CashFlowEvent(at, Decimal("100"), "test"),
        at,
        Decimal("100"),
        profile,
    )
    assert first is not None and second is not None
    assert first["fixed_order_fee_paid"] == second["fixed_order_fee_paid"] == Decimal("0.50")
    assert second["trading_fee_paid"] == first["trading_fee_paid"] * 10


def test_profile_loader_is_strict_and_digest_is_stable(tmp_path: Path) -> None:
    source = {
        "schema_version": "execution-cost-profile/v1",
        "cost_profile_id": "strict-profile-v1",
        "profile_version": 1,
        "description": "Strict profile.",
        "profile_kind": "baseline",
        "trading_fee_ratio": "0.002600",
        "half_spread_ratio": "0",
        "fixed_order_fee": "0",
        "minimum_order_amount": "1.00",
        "below_minimum_behavior": "carry_forward",
    }
    path = tmp_path / "strict-profile-v1.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    first = load_cost_profile(path)
    source["trading_fee_ratio"] = Decimal("0.0026")
    source["minimum_order_amount"] = Decimal("1")
    second = loads_cost_profile(json.dumps(source, default=str))
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.digest == second.digest

    with pytest.raises(ValueError, match="duplicate object key"):
        loads_cost_profile(
            '{"schema_version":"execution-cost-profile/v1",'
            '"schema_version":"execution-cost-profile/v1"}'
        )


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"profile_kind": "baseline"}, "only by explicitly labelled sensitivity"),
        ({"minimum_order_amount": "0.50"}, "must exceed the fixed-fee break-even"),
        ({"below_minimum_behavior": "drop"}, "must be one of"),
        ({"half_spread_ratio": "0.11"}, "at most"),
    ],
)
def test_invalid_profiles_fail_closed(change, match) -> None:
    values = {
        "cost_profile_id": "invalid-profile-v1",
        "profile_version": 1,
        "description": "Invalid profile.",
        "profile_kind": "sensitivity",
        "trading_fee_ratio": "0.01",
        "half_spread_ratio": "0.02",
        "fixed_order_fee": "0.50",
        "minimum_order_amount": "1",
        "below_minimum_behavior": "carry_forward",
    }
    values.update(change)
    with pytest.raises(ValueError, match=match):
        ExecutionCostProfile(**values)


def test_resolve_profile_by_identifier_or_legacy_fee(tmp_path: Path) -> None:
    profile = _profile(
        cost_profile_id="named-profile-v1",
        fixed_order_fee="0",
        profile_kind="baseline",
    )
    path = tmp_path / "named-profile-v1.json"
    path.write_text(json.dumps(profile.canonical_payload()), encoding="utf-8")
    resolved = resolve_cost_profile("named-profile-v1", search_dir=tmp_path)
    assert resolved.digest == profile.digest

    legacy = resolve_cost_profile(None, legacy_fee_ratio="0.004")
    assert legacy.trading_fee_ratio == Decimal("0.004")
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_cost_profile(
            "named-profile-v1",
            legacy_fee_ratio="0.004",
            search_dir=tmp_path,
        )


def test_cost_summary_keeps_components_separate() -> None:
    candles = _candles()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    profile = _profile()
    purchases = [
        execute_costed_purchase(
            candles,
            CashFlowEvent(at, Decimal(amount), "test"),
            at,
            Decimal(amount),
            profile,
        )
        for amount in ("10", "20")
    ]
    rows = [row for row in purchases if row is not None]
    summary = execution_cost_summary(rows, profile)
    assert summary["order_count"] == 2
    assert summary["gross_order_total"] == "30"
    assert summary["average_order_size"] == "15"
    assert Decimal(summary["explicit_fees_paid"]) == (
        Decimal(summary["trading_fees_paid"])
        + Decimal(summary["fixed_order_fees_paid"])
    )
    assert Decimal(summary["total_execution_cost"]) == (
        Decimal(summary["explicit_fees_paid"])
        + Decimal(summary["estimated_spread_cost"])
    )
