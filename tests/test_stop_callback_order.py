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


def test_after_fill_uses_current_candle_atr() -> None:
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
    assert trade["stop_updates"][0]["atr"] == Decimal("5")
    assert trade["stop_updates"][0]["candidate_stop_price"] == Decimal("100.0")


def test_newly_tightened_stop_fills_at_stop_not_candle_open() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # The entry candle stays above its after-fill stop. The next candle then
    # crosses only the newly tightened stop, which must fill at that stop price.
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
    assert trade["exit_price"] == Decimal("106.0")


def test_entry_candle_after_fill_stop_uses_low_when_tightened() -> None:
    start = datetime(2026, 4, 14, 16, tzinfo=UTC)
    candle = Candle(
        start,
        Decimal("63911.8"),
        Decimal("63000"),
        Decimal("65000"),
        Decimal("62660.4"),
        Decimal("1155.85"),
        Decimal("1155.85"),
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
    assert trade["stop_updates"][0]["stop_price_after"] == Decimal("62688.3")
    assert trade["exit_price"] == Decimal("62660.4")


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
