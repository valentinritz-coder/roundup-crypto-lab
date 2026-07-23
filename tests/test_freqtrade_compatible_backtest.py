from datetime import UTC, datetime
from decimal import Decimal

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    LifecycleSettings,
    StrategyDecision,
)
from roundup_crypto_lab.freqtrade_compatible_backtest import run_freqtrade_compatible_backtest
from roundup_crypto_lab.investment_plan import InvestmentPlan


def at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def plan() -> InvestmentPlan:
    return InvestmentPlan("100", "40", "0", 1)


def lifecycle() -> LifecycleSettings:
    return LifecycleSettings(Decimal("-0.12"), True, Decimal("2"))


def candle(day: int, open_: str, high: str, low: str, close: str, atr: str) -> Candle:
    return Candle(
        at(day),
        Decimal(open_),
        Decimal(close),
        Decimal(high),
        Decimal(low),
        Decimal(atr),
    )


def test_entry_candle_uses_high_and_current_atr_for_custom_stop() -> None:
    result = run_freqtrade_compatible_backtest(
        [candle(1, "100", "110", "90", "105", "5")],
        plan(),
        at(1),
        at(2),
        lambda state: StrategyDecision(Action.BUY, state.cash),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=lifecycle(),
    )

    trade = result["trades"][0]
    assert trade["initial_stop_price"] == Decimal("88")
    assert trade["stop_updates"] == [
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "current_rate": Decimal("110"),
            "atr": Decimal("5"),
            "candidate_stop_price": Decimal("100"),
            "stop_price_before": Decimal("88"),
            "stop_price_after": Decimal("100"),
        }
    ]
    assert trade["exit_price"] == Decimal("100")
    assert trade["exit_reason"] == "stop_loss"


def test_custom_stop_can_lock_profit_and_never_loosen() -> None:
    result = run_freqtrade_compatible_backtest(
        [
            candle(1, "100", "105", "100", "104", "10"),
            candle(2, "115", "120", "109", "112", "5"),
        ],
        plan(),
        at(1),
        at(3),
        lambda state: (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision()
        ),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
        lifecycle=lifecycle(),
    )

    trade = result["trades"][0]
    assert [update["candidate_stop_price"] for update in trade["stop_updates"]] == [
        Decimal("85"),
        Decimal("110"),
    ]
    assert [update["stop_price_after"] for update in trade["stop_updates"]] == [
        Decimal("88"),
        Decimal("110"),
    ]
    assert trade["exit_price"] == Decimal("110")
    assert trade["exit_price"] > trade["entry_price"]
