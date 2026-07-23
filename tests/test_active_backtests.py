from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    LifecycleSettings,
    StrategyDecision,
    WalletState,
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
        lambda _: StrategyDecision(),
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
        lambda state: (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision()
        ),
    )

    assert result["current_deployed_capital"] == Decimal("100")
    assert result["cumulative_gross_deployed"] == Decimal("100")
    assert result["free_cash"] == Decimal("40")
    trade = result["trades"][0]
    assert trade["trade_id"] == "trade-000001"
    assert trade["entry_gross_stake"] == Decimal("100")
    assert trade["quantity"] == Decimal("1")
    assert trade["exit_reason"] is None


def test_recurring_entry_can_only_use_cash_available_at_its_timestamp() -> None:
    seen_cash: list[Decimal] = []

    def decide(state: object) -> StrategyDecision:
        cash = state.cash  # type: ignore[attr-defined]
        seen_cash.append(cash)
        return (
            StrategyDecision(Action.BUY, cash) if state.timestamp == at(15) else StrategyDecision()
        )

    result = run_active_backtest(
        [candle(1), candle(15)], plan(), at(1), datetime(2026, 2, 1, tzinfo=UTC), decide
    )

    assert seen_cash == [Decimal("100"), Decimal("140")]
    assert result["trades"][0]["entry_gross_stake"] == Decimal("140")  # type: ignore[index]


def test_contribution_during_open_trade_does_not_mutate_historical_stake() -> None:
    result = run_active_backtest(
        [candle(1), candle(15), candle(16)],
        plan(),
        at(1),
        datetime(2026, 2, 1, tzinfo=UTC),
        lambda state: (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision(Action.SELL)
            if state.timestamp == at(16)
            else StrategyDecision()
        ),
    )

    trade = result["trades"][0]
    assert trade["entry_gross_stake"] == Decimal("100")
    assert trade["exit_reason"] == "exit_signal"
    assert trade["exit_price"] == Decimal("100")
    assert trade["net_proceeds"] == Decimal("100")
    assert result["free_cash"] == Decimal("140")
    assert result["current_deployed_capital"] == Decimal("0")
    assert result["cumulative_gross_deployed"] == Decimal("100")


def test_one_shot_mode_keeps_existing_one_shot_semantics() -> None:
    result = run_active_backtest(
        [candle(1), candle(15)],
        plan(),
        at(1),
        datetime(2026, 2, 1, tzinfo=UTC),
        lambda _: StrategyDecision(),
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
            lambda _: StrategyDecision(),
        )


def ohlc(day: int, open_: str, high: str, low: str, close: str, atr: str | None = None) -> Candle:
    return Candle(
        at(day),
        Decimal(open_),
        Decimal(close),
        Decimal(high),
        Decimal(low),
        None if atr is None else Decimal(atr),
    )


def lifecycle(*, custom: bool = False) -> LifecycleSettings:
    return LifecycleSettings(Decimal("-0.12"), custom, Decimal("2") if custom else None)


def test_fixed_stop_and_gap_below_stop_are_distinct_prices() -> None:
    def buy_first(state: WalletState) -> StrategyDecision:
        return (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision()
        )

    touched = run_active_backtest(
        [ohlc(1, "100", "101", "99", "100"), ohlc(2, "100", "105", "87", "100")],
        plan(),
        at(1),
        at(3),
        buy_first,
        lifecycle=lifecycle(),
    )
    gapped = run_active_backtest(
        [ohlc(1, "100", "101", "99", "100"), ohlc(2, "80", "85", "70", "80")],
        plan(),
        at(1),
        at(3),
        buy_first,
        lifecycle=lifecycle(),
    )
    assert touched["trades"][0]["exit_price"] == Decimal("88")
    assert gapped["trades"][0]["exit_price"] == Decimal("80")
    assert gapped["trades"][0]["exit_reason"] == "stop_loss"


def test_atr_stop_is_causal_and_never_loosened() -> None:
    result = run_active_backtest(
        [
            ohlc(1, "100", "100", "100", "100"),
            ohlc(2, "110", "111", "109", "110", "5"),
            ohlc(3, "110", "111", "99", "110", "20"),
        ],
        plan(),
        at(1),
        at(4),
        lambda state: (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision()
        ),
        lifecycle=lifecycle(custom=True),
    )
    # Freqtrade uses each candle high as current_rate. Day 2 raises 88 to
    # 101; day 3's wider ATR cannot lower it.
    assert result["trades"][0]["exit_price"] == Decimal("101")
    updates = result["trades"][0]["stop_updates"]
    assert updates[0]["current_rate"] == Decimal("111")
    assert updates[0]["atr"] == Decimal("5")
    assert updates[0]["candidate_stop_price"] == Decimal("101")


def test_stop_has_priority_over_same_open_signal_exit() -> None:
    result = run_active_backtest(
        [ohlc(1, "100", "100", "100", "100"), ohlc(2, "80", "90", "70", "80")],
        plan(),
        at(1),
        at(3),
        lambda state: (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision(Action.SELL)
        ),
        lifecycle=lifecycle(),
    )
    assert result["trades"][0]["exit_reason"] == "stop_loss"


def test_end_open_position_is_marked_and_not_forced_closed() -> None:
    result = run_active_backtest(
        [ohlc(1, "100", "100", "100", "100"), ohlc(2, "110", "110", "110", "110")],
        plan(),
        at(1),
        at(3),
        lambda state: (
            StrategyDecision(Action.BUY, state.cash)
            if state.timestamp == at(1)
            else StrategyDecision()
        ),
        lifecycle=lifecycle(),
    )
    assert result["end_of_range_position"] == "open_marked_at_final_close"
    assert result["trades"][0]["exit_reason"] is None


def test_custom_stop_is_applied_on_entry_candle_using_high_and_current_atr() -> None:
    result = run_active_backtest(
        [ohlc(1, "100", "110", "90", "105", "5")],
        plan(),
        at(1),
        at(2),
        lambda state: StrategyDecision(Action.BUY, state.cash),
        lifecycle=lifecycle(custom=True),
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
