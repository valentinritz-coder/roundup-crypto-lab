from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.dca_pilots import (
    ImmediateFloorDrawdownReserve,
    KerAdxAccumulation,
    MovingAverageDeviationDca,
    NoSellValueAveraging,
    build_pilot_strategy,
    registered_pilots,
)
from roundup_crypto_lab.dca_registry import load_registry, strategy_provenance
from roundup_crypto_lab.dca_strategy import (
    CausalIndicator,
    DcaBuyOrder,
    DcaDecisionContext,
    PendingCashBucket,
    evaluate_strategy,
)

REGISTRY = Path("config/dca-strategy-registry.json")
START = datetime(2026, 1, 1, tzinfo=UTC)


def context(
    *,
    cash: str = "100",
    cumulative_contributions: str = "100",
    marked_asset_value: str = "0",
    age_days: int = 0,
    indicators: tuple[CausalIndicator, ...] = (),
    state: dict | None = None,
) -> DcaDecisionContext:
    decision_at = START + timedelta(days=age_days)
    available = Decimal(cash)
    pending = () if available == 0 else (PendingCashBucket(START, available),)
    return DcaDecisionContext(
        decision_at=decision_at,
        available_cash=available,
        quantity=Decimal("0"),
        marked_asset_value=Decimal(marked_asset_value),
        cumulative_contributions=Decimal(cumulative_contributions),
        cumulative_fees=Decimal("0"),
        pending_cash=pending,
        prior_candles=(),
        indicators=indicators,
        state={} if state is None else state,
    )


def causal_indicators(**values: str) -> tuple[CausalIndicator, ...]:
    return tuple(
        CausalIndicator(name, Decimal(value), START)
        for name, value in sorted(values.items())
    )


def amount(decision) -> Decimal:
    return sum((order.gross_amount for order in decision.orders), Decimal("0"))


def pilot(strategy_id: str):
    registry = load_registry(REGISTRY)
    return build_pilot_strategy(registry.strategy(strategy_id))


def test_registry_exposes_the_four_preregistered_pilots_in_frozen_order() -> None:
    registry = load_registry(REGISTRY)
    definitions = registered_pilots(registry)

    assert [definition.strategy_id for definition in definitions] == [
        "drawdown-reserve-dca",
        "no-sell-value-averaging",
        "ma-deviation-dca",
        "ker-adx-accumulation",
    ]
    assert all(definition.research_status == "preregistered" for definition in definitions)
    assert all(definition.maximum_pending_cash_age_days == 180 for definition in definitions)

    provenance = strategy_provenance(
        registry,
        "ker-adx-accumulation",
        "a" * 40,
    )
    assert provenance["parameters"]["ker_threshold"] == "0.35"
    assert [row["name"] for row in provenance["indicator_definitions"]] == [
        "adx_14",
        "ker_20",
    ]
    assert all(row["warmup_candles"] == 120 for row in provenance["indicator_definitions"])


def test_factory_builds_each_concrete_pilot_class() -> None:
    assert isinstance(pilot("drawdown-reserve-dca"), ImmediateFloorDrawdownReserve)
    assert isinstance(pilot("no-sell-value-averaging"), NoSellValueAveraging)
    assert isinstance(pilot("ma-deviation-dca"), MovingAverageDeviationDca)
    assert isinstance(pilot("ker-adx-accumulation"), KerAdxAccumulation)


@pytest.mark.parametrize(
    ("drawdown", "expected_amount", "expected_tag"),
    [
        ("0.0999", "50", "immediate_floor_drawdown_reserve.buy-floor"),
        ("0.10", "62.5", "immediate_floor_drawdown_reserve.buy-tier-1"),
        ("0.20", "75", "immediate_floor_drawdown_reserve.buy-tier-2"),
        ("0.30", "100", "immediate_floor_drawdown_reserve.buy-tier-3"),
    ],
)
def test_drawdown_reserve_has_exact_preregistered_tier_boundaries(
    drawdown: str,
    expected_amount: str,
    expected_tag: str,
) -> None:
    strategy = pilot("drawdown-reserve-dca")
    decision = evaluate_strategy(
        strategy,
        context(indicators=causal_indicators(rolling_drawdown=drawdown)),
    )

    assert amount(decision) == Decimal(expected_amount)
    assert decision.decision_tag == expected_tag


def test_drawdown_tier_is_not_released_repeatedly_without_new_cash() -> None:
    strategy = pilot("drawdown-reserve-dca")
    first = evaluate_strategy(
        strategy,
        context(indicators=causal_indicators(rolling_drawdown="0.10")),
    )
    second = evaluate_strategy(
        strategy,
        context(
            cash="37.5",
            indicators=causal_indicators(rolling_drawdown="0.10"),
            state=dict(first.next_state),
        ),
    )

    assert amount(first) == Decimal("62.5")
    assert second.orders == ()
    assert second.decision_tag == "immediate_floor_drawdown_reserve.skip-reserve"
    assert second.next_state["released_drawdown_tier"] == 1


def test_drawdown_reserve_forces_expired_cash_to_deploy() -> None:
    strategy = pilot("drawdown-reserve-dca")
    decision = evaluate_strategy(
        strategy,
        context(
            cash="40",
            age_days=181,
            indicators=causal_indicators(rolling_drawdown="0"),
            state={
                "processed_contributions": Decimal("100"),
                "released_drawdown_tier": 0,
                "decision_count": 1,
            },
        ),
    )

    assert amount(decision) == Decimal("40")
    assert decision.decision_tag == "immediate_floor_drawdown_reserve.buy-cash-expiry"


def test_no_sell_value_averaging_buys_shortfall_and_never_emits_a_sell() -> None:
    strategy = pilot("no-sell-value-averaging")
    below = evaluate_strategy(
        strategy,
        context(cash="40", marked_asset_value="60"),
    )
    above = evaluate_strategy(
        strategy,
        context(
            cash="40",
            marked_asset_value="120",
            state={
                "processed_contributions": Decimal("100"),
                "decision_count": 1,
            },
        ),
    )

    assert amount(below) == Decimal("40")
    assert below.decision_tag == "no_sell_value_averaging.buy-shortfall"
    assert above.orders == ()
    assert above.decision_tag == "no_sell_value_averaging.skip-above-target"


def test_value_averaging_caps_shortfall_to_available_cash_and_expires_reserve() -> None:
    strategy = pilot("no-sell-value-averaging")
    insufficient = evaluate_strategy(
        strategy,
        context(cash="3", marked_asset_value="0"),
    )
    expired = evaluate_strategy(
        strategy,
        context(
            cash="25",
            marked_asset_value="120",
            age_days=181,
            state={
                "processed_contributions": Decimal("100"),
                "decision_count": 2,
            },
        ),
    )

    assert amount(insufficient) == Decimal("3")
    assert amount(expired) == Decimal("25")
    assert expired.decision_tag == "no_sell_value_averaging.buy-cash-expiry"


@pytest.mark.parametrize(
    ("previous_close", "expected_amount", "expected_regime"),
    [
        ("80", "20", "below-ma"),
        ("100", "10", "neutral"),
        ("110", "5", "above-ma"),
    ],
)
def test_moving_average_deviation_covers_bear_flat_and_bull_paths(
    previous_close: str,
    expected_amount: str,
    expected_regime: str,
) -> None:
    strategy = pilot("ma-deviation-dca")
    decision = evaluate_strategy(
        strategy,
        context(
            indicators=causal_indicators(
                long_ma="100",
                previous_close=previous_close,
            )
        ),
    )

    assert amount(decision) == Decimal(expected_amount)
    assert decision.diagnostics["regime"] == expected_regime
    assert decision.decision_tag == f"moving_average_deviation.buy-{expected_regime}"


def test_moving_average_deviation_is_deterministic_on_oscillating_inputs() -> None:
    strategy = pilot("ma-deviation-dca")
    decisions = [
        evaluate_strategy(
            strategy,
            context(
                indicators=causal_indicators(long_ma="100", previous_close=price),
                state={"decision_count": index},
            ),
        )
        for index, price in enumerate(("80", "110", "95", "105"))
    ]
    repeated = [
        evaluate_strategy(
            strategy,
            context(
                indicators=causal_indicators(long_ma="100", previous_close=price),
                state={"decision_count": index},
            ),
        )
        for index, price in enumerate(("80", "110", "95", "105"))
    ]

    assert decisions == repeated
    assert [amount(decision) for decision in decisions] == [
        Decimal("20"),
        Decimal("5"),
        Decimal("20"),
        Decimal("5"),
    ]


def test_ker_adx_only_accelerates_when_both_causal_conditions_hold() -> None:
    strategy = pilot("ker-adx-accumulation")
    accelerated = evaluate_strategy(
        strategy,
        context(indicators=causal_indicators(adx_14="25", ker_20="0.35")),
    )
    floor_only = evaluate_strategy(
        strategy,
        context(indicators=causal_indicators(adx_14="24.99", ker_20="0.35")),
    )
    waiting = evaluate_strategy(
        strategy,
        context(
            cash="90",
            indicators=causal_indicators(adx_14="20", ker_20="0.20"),
            state={
                "processed_contributions": Decimal("100"),
                "decision_count": 1,
                "signal_count": 0,
            },
        ),
    )

    assert amount(accelerated) == Decimal("55")
    assert accelerated.decision_tag == "ker_adx_accumulation.buy-accelerated"
    assert amount(floor_only) == Decimal("10")
    assert floor_only.decision_tag == "ker_adx_accumulation.buy-immediate-floor"
    assert waiting.orders == ()
    assert waiting.decision_tag == "ker_adx_accumulation.skip-wait-signal"


def test_ker_adx_forces_old_reserve_and_preserves_exact_state_transition() -> None:
    strategy = pilot("ker-adx-accumulation")
    ctx = context(
        cash="30",
        age_days=181,
        indicators=causal_indicators(adx_14="10", ker_20="0.10"),
        state={
            "processed_contributions": Decimal("100"),
            "decision_count": 4,
            "signal_count": 2,
        },
    )
    first = evaluate_strategy(strategy, ctx)
    second = evaluate_strategy(strategy, ctx)

    assert first == second
    assert amount(first) == Decimal("30")
    assert first.decision_tag == "ker_adx_accumulation.buy-cash-expiry"
    assert first.next_state["decision_count"] == 5
    assert first.next_state["signal_count"] == 2


def test_pilots_fail_closed_on_missing_or_future_indicators() -> None:
    with pytest.raises(ValueError, match="missing required causal indicator"):
        evaluate_strategy(pilot("ma-deviation-dca"), context())

    with pytest.raises(ValueError, match="future indicator"):
        context(
            indicators=(
                CausalIndicator(
                    "rolling_drawdown",
                    Decimal("0.2"),
                    START + timedelta(days=1),
                ),
            )
        )


def test_every_pilot_is_buy_only_and_cannot_overspend() -> None:
    cases = [
        (
            pilot("drawdown-reserve-dca"),
            context(cash="7", indicators=causal_indicators(rolling_drawdown="0.30")),
        ),
        (
            pilot("no-sell-value-averaging"),
            context(cash="7", marked_asset_value="0"),
        ),
        (
            pilot("ma-deviation-dca"),
            context(
                cash="7",
                indicators=causal_indicators(long_ma="100", previous_close="80"),
            ),
        ),
        (
            pilot("ker-adx-accumulation"),
            context(cash="7", indicators=causal_indicators(adx_14="30", ker_20="0.5")),
        ),
    ]

    for strategy, ctx in cases:
        decision = evaluate_strategy(strategy, ctx)
        assert all(isinstance(order, DcaBuyOrder) for order in decision.orders)
        assert amount(decision) <= ctx.available_cash
