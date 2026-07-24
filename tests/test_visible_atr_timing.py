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


def test_entry_callbacks_use_same_bridge_visible_atr_without_second_shift() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [
        Candle(start, Decimal("100"), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("13")),
        Candle(
            start + timedelta(hours=4),
            Decimal("100"),
            Decimal("104"),
            Decimal("106"),
            Decimal("95"),
            Decimal("14"),
        ),
    ]
    calls = iter([StrategyDecision(), StrategyDecision(Action.BUY, Decimal("32"))])
    result = run_active_backtest(
        candles,
        InvestmentPlan("40", "40", "0.0026", 1),
        start,
        start + timedelta(hours=8),
        lambda _: next(calls),
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

    updates = result["trades"][0]["stop_updates"]
    assert [update["after_fill"] for update in updates] == [True, False]
    assert [update["atr"] for update in updates] == [Decimal("14"), Decimal("14")]
    assert [update["candidate_stop_price"] for update in updates] == [
        Decimal("72.0"),
        Decimal("78.0"),
    ]
