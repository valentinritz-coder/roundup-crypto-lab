from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.dca_strategy import (
    CausalIndicator,
    DcaBuyOrder,
    DcaDecision,
    PendingCashBucket,
    build_decision_context,
    decision_artifact_bytes,
    evaluate_strategy,
    execute_decision,
    validate_decision,
)
from roundup_crypto_lab.investment_plan import CashFlowEvent

START = datetime(2026, 1, 1, tzinfo=UTC)


def candles() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (START + timedelta(hours=hours), 100 + hours, 110 + hours, 90 + hours, 105 + hours, 1)
            for hours in (0, 4, 8, 12)
        ],
        columns=["date", "open", "high", "low", "close", "volume"],
    )


def context(*, decision_at: datetime = START + timedelta(hours=8), cash: str = "100"):
    return build_decision_context(
        decision_at=decision_at,
        available_cash=cash,
        quantity="1",
        marked_asset_value="100",
        cumulative_contributions="200",
        cumulative_fees="1",
        pending_cash=(PendingCashBucket(START, Decimal(cash)),),
        candles=candles(),
        indicators=(CausalIndicator("adx", Decimal("25"), START + timedelta(hours=4)),),
        state={"count": 1},
    )


class StatelessStrategy:
    strategy_id = "test.stateless"
    strategy_version = "v1"

    def decide(self, ctx):
        return DcaDecision(
            "buy.fixed",
            (DcaBuyOrder(Decimal("25"), "fixed"),),
            {"prior_candles": len(ctx.prior_candles)},
        )


class StatefulStrategy:
    strategy_id = "test.stateful"
    strategy_version = "v1"

    def decide(self, ctx):
        count = int(ctx.state.get("count", 0))
        return DcaDecision("skip.wait", next_state={"count": count + 1})


def test_context_contains_only_completed_prior_candles() -> None:
    ctx = context()
    assert [candle.opened_at for candle in ctx.prior_candles] == [
        START,
        START + timedelta(hours=4),
    ]

    changed = candles()
    changed.loc[changed["date"] >= START + timedelta(hours=8), "close"] = 999999
    rebuilt = build_decision_context(
        decision_at=ctx.decision_at,
        available_cash=ctx.available_cash,
        quantity=ctx.quantity,
        marked_asset_value=ctx.marked_asset_value,
        cumulative_contributions=ctx.cumulative_contributions,
        cumulative_fees=ctx.cumulative_fees,
        pending_cash=ctx.pending_cash,
        candles=changed,
        indicators=ctx.indicators,
        state=ctx.state,
    )
    assert rebuilt.prior_candles == ctx.prior_candles


def test_future_cash_and_indicator_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="future contribution"):
        build_decision_context(
            decision_at=START,
            available_cash="100",
            quantity="0",
            marked_asset_value="0",
            cumulative_contributions="100",
            cumulative_fees="0",
            pending_cash=(PendingCashBucket(START + timedelta(hours=4), Decimal("100")),),
            candles=candles(),
        )
    with pytest.raises(ValueError, match="future indicator"):
        build_decision_context(
            decision_at=START,
            available_cash="100",
            quantity="0",
            marked_asset_value="0",
            cumulative_contributions="100",
            cumulative_fees="0",
            pending_cash=(PendingCashBucket(START, Decimal("100")),),
            candles=candles(),
            indicators=(CausalIndicator("adx", Decimal("25"), START + timedelta(hours=4)),),
        )


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity"])
def test_invalid_order_amounts_fail_closed(amount: str) -> None:
    with pytest.raises(ValueError):
        DcaBuyOrder(Decimal(amount), "invalid")


def test_decision_cannot_overspend_available_cash() -> None:
    with pytest.raises(ValueError, match="available cash"):
        validate_decision(
            context(cash="100"),
            DcaDecision("buy.too-much", (DcaBuyOrder(Decimal("100.01"), "too-much"),)),
        )


def test_stateful_stateless_and_skipped_decisions_are_auditable() -> None:
    stateless = StatelessStrategy()
    stateless_decision = evaluate_strategy(stateless, context())
    assert stateless_decision.orders[0].gross_amount == Decimal("25")

    stateful = StatefulStrategy()
    skipped = evaluate_strategy(stateful, context())
    assert skipped.orders == ()
    assert skipped.decision_tag == "skip.wait"
    assert skipped.next_state["count"] == 2


def test_decision_artifacts_are_byte_stable() -> None:
    ctx = context()
    strategy = StatelessStrategy()
    first = DcaDecision(
        "buy.fixed",
        (DcaBuyOrder(Decimal("25.00"), "fixed"),),
        {"z": Decimal("2"), "a": {"later": False, "count": 1}},
        {"b": 2, "a": 1},
    )
    second = DcaDecision(
        "buy.fixed",
        (DcaBuyOrder(Decimal("25.00"), "fixed"),),
        {"a": {"count": 1, "later": False}, "z": Decimal("2")},
        {"a": 1, "b": 2},
    )
    assert decision_artifact_bytes(strategy, ctx, first) == decision_artifact_bytes(
        strategy, ctx, second
    )


def test_execution_defers_to_first_eligible_candle_and_missing_candles() -> None:
    frame = candles()
    frame = frame[frame["date"] != START + timedelta(hours=4)].reset_index(drop=True)
    decision_at = START + timedelta(hours=4)
    ctx = build_decision_context(
        decision_at=decision_at,
        available_cash="100",
        quantity="0",
        marked_asset_value="0",
        cumulative_contributions="100",
        cumulative_fees="0",
        pending_cash=(PendingCashBucket(START, Decimal("100")),),
        candles=frame,
    )
    executions = execute_decision(
        candles=frame,
        funding_event=CashFlowEvent(START, Decimal("100"), "initial"),
        context=ctx,
        decision=DcaDecision("buy.now", (DcaBuyOrder(Decimal("100"), "all"),)),
        fee_ratio="0",
    )
    assert executions[0]["scheduled_at"] == decision_at.isoformat()
    assert executions[0]["executed_at"] == (START + timedelta(hours=8)).isoformat()


def test_no_future_candle_means_no_execution() -> None:
    frame = candles().iloc[:2].copy()
    decision_at = START + timedelta(hours=12)
    ctx = build_decision_context(
        decision_at=decision_at,
        available_cash="100",
        quantity="0",
        marked_asset_value="0",
        cumulative_contributions="100",
        cumulative_fees="0",
        pending_cash=(PendingCashBucket(START, Decimal("100")),),
        candles=frame,
    )
    assert execute_decision(
        candles=frame,
        funding_event=CashFlowEvent(START, Decimal("100"), "initial"),
        context=ctx,
        decision=DcaDecision("buy.now", (DcaBuyOrder(Decimal("100"), "all"),)),
        fee_ratio="0",
    ) == []


def test_context_state_is_immutable() -> None:
    ctx = context()
    with pytest.raises(TypeError):
        ctx.state["count"] = 2


def test_execution_validates_before_calling_purchase(monkeypatch) -> None:
    import roundup_crypto_lab.dca_strategy as module

    called = False

    def forbidden_purchase(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("purchase must not run")

    monkeypatch.setattr(module, "purchase", forbidden_purchase)
    with pytest.raises(ValueError, match="available cash"):
        execute_decision(
            candles=candles(),
            funding_event=CashFlowEvent(START, Decimal("100"), "initial"),
            context=context(cash="100"),
            decision=DcaDecision(
                "buy.too-much",
                (DcaBuyOrder(Decimal("100.01"), "all"),),
            ),
            fee_ratio="0",
        )
    assert called is False
