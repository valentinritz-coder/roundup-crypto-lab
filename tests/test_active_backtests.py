from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    StrategyDecision,
    run_active_backtest,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan


def at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def plan() -> InvestmentPlan:
    return InvestmentPlan(Decimal("100"), Decimal("40"), Decimal("0"), 15)


def candle(day: int, price: str = "100") -> Candle:
    value = Decimal(price)
    return Candle(at(day), value, value)


def test_no_trade_cash_equals_contributions_and_return_is_zero() -> None:
    result = run_active_backtest(
        [candle(1), candle(15)],
        plan(),
        at(1),
        datetime(2026, 2, 1, tzinfo=UTC),
        lambda *_: StrategyDecision(),
    )

    assert result["free_cash"] == Decimal("140")
    assert result["total_contributed_capital"] == Decimal("140")
    assert result["investment_return"] == Decimal("0")
    assert len(result["contribution_ledger"]) == 2


def test_buy_once_leaves_later_contribution_as_cash() -> None:
    result = run_active_backtest(
        [candle(1), candle(15)],
        plan(),
        at(1),
        datetime(2026, 2, 1, tzinfo=UTC),
        lambda current, state: (
            StrategyDecision(Action.BUY, state.cash)
            if current.timestamp == at(1)
            else StrategyDecision()
        ),
    )

    assert result["deployed_capital"] == Decimal("100")
    assert result["free_cash"] == Decimal("40")
    assert result["trades"] == [
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "side": "buy",
            "gross_stake": Decimal("100"),
            "price": Decimal("100"),
            "fee_paid": Decimal("0"),
            "quantity": Decimal("1"),
        }
    ]


def test_recurring_entry_can_only_use_cash_available_at_its_timestamp() -> None:
    seen_cash: list[Decimal] = []

    def decide(current: Candle, state: object) -> StrategyDecision:
        cash = state.cash  # type: ignore[attr-defined]
        seen_cash.append(cash)
        return (
            StrategyDecision(Action.BUY, cash)
            if current.timestamp == at(15)
            else StrategyDecision()
        )

    result = run_active_backtest(
        [candle(1), candle(15)], plan(), at(1), datetime(2026, 2, 1, tzinfo=UTC), decide
    )

    assert seen_cash == [Decimal("100"), Decimal("140")]
    assert result["trades"][0]["gross_stake"] == Decimal("140")  # type: ignore[index]


def test_contribution_during_open_trade_does_not_mutate_historical_stake() -> None:
    result = run_active_backtest(
        [candle(1), candle(15), candle(16)],
        plan(),
        at(1),
        datetime(2026, 2, 1, tzinfo=UTC),
        lambda current, state: (
            StrategyDecision(Action.BUY, state.cash)
            if current.timestamp == at(1)
            else StrategyDecision(Action.SELL)
            if current.timestamp == at(16)
            else StrategyDecision()
        ),
    )

    assert result["trades"][0]["gross_stake"] == Decimal("100")  # type: ignore[index]
    assert result["trades"][1]["gross_stake"] == Decimal("100")  # type: ignore[index]
    assert result["free_cash"] == Decimal("140")


def test_one_shot_mode_keeps_existing_one_shot_semantics() -> None:
    result = run_active_backtest(
        [candle(1), candle(15)],
        plan(),
        at(1),
        datetime(2026, 2, 1, tzinfo=UTC),
        lambda *_: StrategyDecision(),
        mode=CapitalMode.ONE_SHOT_CAPITAL,
    )
    assert result["capital_mode"] == "one_shot_capital"
    assert result["free_cash"] == Decimal("100")


def test_end_exclusive_timerange_rejects_end_candle_and_never_credits_its_cashflow() -> None:
    with pytest.raises(ValueError, match="end-exclusive"):
        run_active_backtest(
            [candle(1), Candle(datetime(2026, 2, 1, tzinfo=UTC), Decimal("100"), Decimal("100"))],
            plan(),
            at(1),
            datetime(2026, 2, 1, tzinfo=UTC),
            lambda *_: StrategyDecision(),
        )
