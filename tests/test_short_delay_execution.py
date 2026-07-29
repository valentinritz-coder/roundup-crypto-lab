from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.execution_costs import ExecutionCostProfile, execute_costed_purchase
from roundup_crypto_lab.investment_plan import CashFlowEvent
from roundup_crypto_lab.short_delay_execution import (
    completed_daily_closes,
    execute_short_delay_strategy,
)


def profile(
    *,
    spread: str = "0",
    fixed_fee: str = "0",
    minimum: str = "0",
) -> ExecutionCostProfile:
    return ExecutionCostProfile(
        cost_profile_id=f"test-{spread}-{fixed_fee}-{minimum}".replace(".", "-") ,
        profile_version=1,
        description="Test execution assumptions.",
        profile_kind="sensitivity" if fixed_fee != "0" else "baseline",
        trading_fee_ratio=Decimal("0.0025"),
        half_spread_ratio=Decimal(spread),
        fixed_order_fee=Decimal(fixed_fee),
        minimum_order_amount=Decimal(minimum),
        below_minimum_behavior="carry_forward",
    )


def candles(
    start: datetime,
    daily_closes: list[Decimal],
    *,
    omit: set[datetime] | None = None,
) -> pd.DataFrame:
    omitted = omit or set()
    rows = []
    for day_index, daily_close in enumerate(daily_closes):
        day_start = start + timedelta(days=day_index)
        for hour in (0, 4, 8, 12, 16, 20):
            timestamp = day_start + timedelta(hours=hour)
            if timestamp in omitted:
                continue
            price = daily_close if hour == 20 else daily_close + Decimal("1")
            rows.append(
                {
                    "date": timestamp,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": Decimal("1"),
                }
            )
    return pd.DataFrame(rows)


def event(at: datetime, amount: str = "1000") -> CashFlowEvent:
    return CashFlowEvent(at, Decimal(amount), "monthly")


def test_completed_daily_closes_rejects_missing_candle() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frame = candles(
        start,
        [Decimal("100")],
        omit={start + timedelta(hours=8)},
    )
    with pytest.raises(ValueError, match="incomplete UTC daily observation"):
        completed_daily_closes(frame)


def test_control_matches_direct_monthly_execution() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    contribution = event(start + timedelta(days=12))
    frame = candles(start, [Decimal("100") + index for index in range(30)])
    cost_profile = profile()

    result = execute_short_delay_strategy(
        strategy_id="monthly_dca_control",
        events=(contribution,),
        candles=frame,
        pair="BTC/EUR",
        profile=cost_profile,
    )
    direct = execute_costed_purchase(
        frame,
        contribution,
        contribution.contributed_at,
        contribution.amount,
        cost_profile,
    )

    assert direct is not None
    purchase = result["purchase_ledger"][0]
    assert purchase["executed_at"] == direct["executed_at"]
    assert Decimal(str(purchase["execution_price"])) == direct["execution_price"]
    assert Decimal(str(purchase["quantity"])) == direct["quantity"]
    assert result["delay_diagnostics"]["delayed_contribution_count"] == 0


def test_rising_market_invests_immediately() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    contribution_at = start + timedelta(days=12)
    frame = candles(start, [Decimal("100") + index for index in range(30)])
    result = execute_short_delay_strategy(
        strategy_id="negative_7d_return_delay",
        events=(event(contribution_at),),
        candles=frame,
        pair="BTC/EUR",
        profile=profile(),
    )

    allocation = result["funding_allocations"][0]
    assert allocation["release_type"] == "immediate"
    assert allocation["scheduled_at"] == contribution_at.isoformat()
    assert result["signal_ledger"][0]["action"] == "execute"


def test_decline_releases_after_signal_clears() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = [Decimal("150") - index for index in range(20)]
    closes[13] = Decimal("170")
    frame = candles(start, closes)
    contribution_at = start + timedelta(days=13)
    result = execute_short_delay_strategy(
        strategy_id="negative_7d_return_delay",
        events=(event(contribution_at),),
        candles=frame,
        pair="BTC/EUR",
        profile=profile(),
    )

    allocation = result["funding_allocations"][0]
    assert allocation["release_type"] == "signal_release"
    assert allocation["scheduled_at"] == (contribution_at + timedelta(days=1)).isoformat()
    assert [row["action"] for row in result["signal_ledger"]] == ["delay", "execute"]


def test_confirmed_decline_releases_on_next_positive_close() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = [Decimal("200") - index for index in range(25)]
    closes[19] = Decimal("190")
    closes[20] = Decimal("191")
    frame = candles(start, closes)
    contribution_at = start + timedelta(days=20)
    result = execute_short_delay_strategy(
        strategy_id="confirmed_short_decline_delay",
        events=(event(contribution_at),),
        candles=frame,
        pair="BTC/EUR",
        profile=profile(),
    )

    assert result["funding_allocations"][0]["release_type"] == "signal_release"
    assert result["signal_ledger"][-1]["reason"] == "first_positive_daily_close"


def test_forced_deployment_occurs_on_exact_day_seven_across_year_boundary() -> None:
    start = datetime(2026, 12, 1, tzinfo=UTC)
    frame = candles(start, [Decimal("300") - index for index in range(50)])
    contribution_at = datetime(2026, 12, 28, tzinfo=UTC)
    result = execute_short_delay_strategy(
        strategy_id="negative_7d_return_delay",
        events=(event(contribution_at),),
        candles=frame,
        pair="BTC/EUR",
        profile=profile(),
    )

    allocation = result["funding_allocations"][0]
    assert allocation["release_type"] == "forced"
    assert allocation["scheduled_at"] == datetime(2027, 1, 4, tzinfo=UTC).isoformat()
    assert result["delay_diagnostics"]["maximum_delay_days"] == Decimal("7")
    assert result["delay_diagnostics"]["final_pending_cash"] == Decimal("0")


def test_future_candle_cannot_change_earlier_decision() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = [Decimal("100") + index for index in range(20)]
    changed = list(base)
    changed[15] = Decimal("1")
    contribution_at = start + timedelta(days=12)
    common = {
        "strategy_id": "below_7d_sma_delay",
        "events": (event(contribution_at),),
        "pair": "BTC/EUR",
        "profile": profile(),
    }
    first = execute_short_delay_strategy(candles=candles(start, base), **common)
    second = execute_short_delay_strategy(candles=candles(start, changed), **common)

    assert first["funding_allocations"][0]["scheduled_at"] == contribution_at.isoformat()
    assert second["funding_allocations"][0]["scheduled_at"] == contribution_at.isoformat()
    assert first["signal_ledger"] == second["signal_ledger"]


def test_contribution_buckets_never_consume_future_contributions() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frame = candles(start, [Decimal("300") - index for index in range(70)])
    first = event(start + timedelta(days=20), "100")
    second = event(start + timedelta(days=50), "200")
    result = execute_short_delay_strategy(
        strategy_id="negative_7d_return_delay",
        events=(second, first),
        candles=frame,
        pair="BTC/EUR",
        profile=profile(),
    )

    allocations = result["funding_allocations"]
    assert [row["funded_amount"] for row in allocations] == [Decimal("100"), Decimal("200")]
    assert all(len(row["funding_allocations"]) == 1 for row in result["purchase_ledger"])
    assert sum((row["funded_amount"] for row in allocations), Decimal("0")) == Decimal("300")


@pytest.mark.parametrize(
    "cost_profile",
    [
        profile(),
        profile(spread="0.001"),
        profile(spread="0.002", fixed_fee="1", minimum="10"),
    ],
)
def test_all_cost_components_are_conserved(cost_profile: ExecutionCostProfile) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frame = candles(start, [Decimal("100") + index for index in range(30)])
    result = execute_short_delay_strategy(
        strategy_id="monthly_dca_control",
        events=(event(start + timedelta(days=12)),),
        candles=frame,
        pair="BTC/EUR",
        profile=cost_profile,
    )

    allocation = result["funding_allocations"][0]
    purchase = result["purchase_ledger"][0]
    gross = Decimal(str(allocation["funded_amount"]))
    fees = Decimal(str(allocation["explicit_fees"]))
    quantity = Decimal(str(allocation["btc_quantity"]))
    price = Decimal(str(allocation["execution_price"]))
    assert gross - fees == Decimal(str(purchase["net_contribution"]))
    assert quantity == Decimal(str(purchase["quantity"]))
    assert quantity * price == Decimal(str(purchase["net_contribution"]))
    assert result["execution_costs"]["cost_profile"]["profile_digest"] == cost_profile.digest
