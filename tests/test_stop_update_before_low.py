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


def test_existing_trade_updates_stop_before_testing_intrabar_low() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
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
            Decimal("104"),
            Decimal("110"),
            Decimal("103"),
            Decimal("2"),
        ),
    ]
    decisions = iter([
        StrategyDecision(Action.BUY, Decimal("32")),
        StrategyDecision(),
    ])
    result = run_active_backtest(
        candles,
        InvestmentPlan("40", "40", "0.0026", 1),
        start,
        start + timedelta(hours=8),
        lambda _: next(decisions),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=LifecycleSettings(
            Decimal("-0.12"),
            True,
            Decimal("2"),
            True,
            Decimal("0.1"),
            Decimal("0.00000001"),
        ),
    )

    trade = result["trades"][0]
    # The bridge supplies the ATR visible to the callback on each execution candle.
    # The engine must use the second candle's supplied ATR 2 without shifting again.
    assert trade["stop_updates"][-1]["atr"] == Decimal("2")
    assert trade["stop_updates"][-1]["stop_price_before"] == Decimal("100.0")
    assert trade["stop_updates"][-1]["stop_price_after"] == Decimal("106.0")
    assert trade["exit_price"] == Decimal("106.0")


def test_existing_stop_gap_still_fills_at_open_before_callback() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
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
            Decimal("99"),
            Decimal("105"),
            Decimal("110"),
            Decimal("98"),
            Decimal("2"),
        ),
    ]
    decisions = iter([
        StrategyDecision(Action.BUY, Decimal("32")),
        StrategyDecision(),
    ])
    result = run_active_backtest(
        candles,
        InvestmentPlan("40", "40", "0.0026", 1),
        start,
        start + timedelta(hours=8),
        lambda _: next(decisions),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=LifecycleSettings(
            Decimal("-0.12"),
            True,
            Decimal("2"),
            True,
            Decimal("0.1"),
            Decimal("0.00000001"),
        ),
    )

    trade = result["trades"][0]
    assert trade["exit_price"] == Decimal("99")
    # No regular callback is made on the gap candle.
    assert trade["stop_updates"][-1]["timestamp"] == start.isoformat()
