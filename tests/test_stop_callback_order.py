from datetime import UTC, datetime, timedelta
from decimal import Decimal

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    LifecycleSettings,
    StrategyDecision,
    run_active_backtest,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan


def _plan() -> InvestmentPlan:
    return InvestmentPlan("40", "40", "0.0026", 1)


def _lifecycle() -> LifecycleSettings:
    return LifecycleSettings(
        Decimal("-0.12"),
        True,
        Decimal("2"),
        True,
        Decimal("0.1"),
        Decimal("0.00000001"),
    )


def test_after_fill_uses_entry_rate_and_visible_atr() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candle = Candle(
        start,
        Decimal("100"),
        Decimal("98"),
        Decimal("110"),
        Decimal("90"),
        Decimal("2"),
        Decimal("5"),
    )
    result = run_active_backtest(
        [candle],
        _plan(),
        start,
        start + timedelta(hours=4),
        lambda _: StrategyDecision(Action.BUY, Decimal("32")),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=_lifecycle(),
    )
    trade = result["trades"][0]
    update = trade["stop_updates"][0]
    assert update["current_rate"] == Decimal("100")
    assert update["atr"] == Decimal("5")
    assert update["candidate_stop_price"] == Decimal("90.0")
    assert trade["exit_price"] == Decimal("90.0")
    assert len(trade["stop_updates"]) == 1


def test_newly_tightened_stop_fills_at_stop_not_candle_open() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # The entry callback uses the 100 fill and leaves a stop at 94. Because that
    # survives, the same entry candle also receives a normal callback at its 106 high.
    # The next candle then tightens from 100 to 106 and crosses that new stop.
    candles = [
        Candle(
            start,
            Decimal("100"),
            Decimal("105"),
            Decimal("106"),
            Decimal("101"),
            Decimal("3"),
            Decimal("3"),
        ),
        Candle(
            start + timedelta(hours=4),
            Decimal("101"),
            Decimal("103"),
            Decimal("110"),
            Decimal("103"),
            Decimal("2"),
            Decimal("2"),
        ),
    ]
    calls = iter([StrategyDecision(Action.BUY, Decimal("32")), StrategyDecision()])
    result = run_active_backtest(
        candles,
        _plan(),
        start,
        start + timedelta(hours=8),
        lambda _: next(calls),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=_lifecycle(),
    )
    trade = result["trades"][0]
    assert [update["candidate_stop_price"] for update in trade["stop_updates"]] == [
        Decimal("94.0"),
        Decimal("100.0"),
        Decimal("106.0"),
    ]
    assert trade["exit_price"] == Decimal("106.0")


def test_entry_candle_after_fill_matches_native_entry_rate_case() -> None:
    start = datetime(2026, 5, 11, tzinfo=UTC)
    candle = Candle(
        start,
        Decimal("69850.0"),
        Decimal("69000"),
        Decimal("69996.8"),
        Decimal("68480.8"),
        Decimal("577.1254911442212"),
        Decimal("577.1254911442212"),
    )
    result = run_active_backtest(
        [candle],
        _plan(),
        start,
        start + timedelta(hours=4),
        lambda _: StrategyDecision(Action.BUY, Decimal("32")),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=_lifecycle(),
    )
    trade = result["trades"][0]
    update = trade["stop_updates"][0]
    assert update["current_rate"] == Decimal("69850.0")
    assert update["stop_price_after"] == Decimal("68695.8")
    assert trade["exit_price"] == Decimal("68695.8")
    assert len(trade["stop_updates"]) == 1


def test_entry_candle_runs_normal_callback_when_after_fill_stop_survives() -> None:
    start = datetime(2026, 3, 4, tzinfo=UTC)
    candle = Candle(
        start,
        Decimal("63098.5"),
        Decimal("63000"),
        Decimal("63738.5"),
        Decimal("60931.4"),
        Decimal("1403.500308265872"),
        Decimal("1403.500308265872"),
    )
    result = run_active_backtest(
        [candle],
        _plan(),
        start,
        start + timedelta(hours=4),
        lambda _: StrategyDecision(Action.BUY, Decimal("32")),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=_lifecycle(),
    )
    trade = result["trades"][0]
    assert [update["after_fill"] for update in trade["stop_updates"]] == [True, False]
    assert [update["current_rate"] for update in trade["stop_updates"]] == [
        Decimal("63098.5"),
        Decimal("63738.5"),
    ]
    assert [update["stop_price_after"] for update in trade["stop_updates"]] == [
        Decimal("60291.5"),
        Decimal("60931.5"),
    ]
    assert trade["exit_price"] == Decimal("60931.5")


def test_entry_candle_fixed_stop_keeps_stop_level_fill_without_custom_update() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candle = Candle(
        start,
        Decimal("120"),
        Decimal("120"),
        Decimal("121"),
        Decimal("100"),
    )
    lifecycle = LifecycleSettings(
        Decimal("-0.12"),
        False,
        None,
        True,
        Decimal("0.1"),
        Decimal("0.00000001"),
    )
    result = run_active_backtest(
        [candle],
        _plan(),
        start,
        start + timedelta(hours=4),
        lambda _: StrategyDecision(Action.BUY, Decimal("32")),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=lifecycle,
    )
    trade = result["trades"][0]
    assert trade["initial_stop_price"] == Decimal("105.6")
    assert trade["stop_updates"] == []
    assert trade["exit_price"] == Decimal("105.6")
