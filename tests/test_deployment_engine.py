from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.deployment_engine import WEEKDAYS, build_result, deploy
from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan
from roundup_crypto_lab.scenario_passive import _monthly_deploy


def constant_candles(start: datetime, end: datetime) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="4h", inclusive="left")
    return pd.DataFrame(
        [(timestamp, 10, 10, 10, 10, 1) for timestamp in dates],
        columns=["date", "open", "high", "low", "close", "volume"],
    )


def economic_projection(result: dict) -> dict:
    return {
        "number_of_buys": result["number_of_buys"],
        "total_contributions": result["total_contributions"],
        "quantity_exact": result["quantity_exact"],
        "cash_balance_exact": result["cash_balance_exact"],
        "fees_paid": result["fees_paid"],
        "final_value_exact": result["final_value_exact"],
        "max_drawdown_raw_portfolio": result["max_drawdown_raw_portfolio"],
        "max_drawdown_time_weighted": result["max_drawdown_time_weighted"],
        "purchase_ledger": result["purchase_ledger"],
    }


def test_golden_engine_outputs_preserve_immediate_daily_and_weekly_economics() -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = datetime(2026, 1, 8, tzinfo=UTC)
    candles = constant_candles(start, end)
    plan = InvestmentPlan("120", "1", "0.1", 23)
    events = (CashFlowEvent(start, Decimal("120"), "initial"),)

    results = {}
    for method in ("immediate", "daily_dca", "weekly_dca"):
        purchases = deploy(plan, events, candles, method, WEEKDAYS["monday"])
        results[method] = economic_projection(
            build_result(method, "BTC/EUR", candles, events, purchases)
        )

    assert results["immediate"] == {
        "number_of_buys": 1,
        "total_contributions": 120.0,
        "quantity_exact": "10.8",
        "cash_balance_exact": "0",
        "fees_paid": 12.0,
        "final_value_exact": "108.0",
        "max_drawdown_raw_portfolio": 0.0,
        "max_drawdown_time_weighted": 0.0,
        "purchase_ledger": [
            {
                "contributed_at": "2026-01-05T00:00:00+00:00",
                "scheduled_at": "2026-01-05T00:00:00+00:00",
                "executed_at": "2026-01-05T00:00:00+00:00",
                "execution_price": "10",
                "gross_contribution": "120",
                "fee_paid": "12.0",
                "net_contribution": "108.0",
                "quantity": "10.8",
                "cumulative_quantity": "10.8",
                "cumulative_gross_contributions": "120",
                "cumulative_fees": "12.0",
                "residual_cash": "0",
                "marked_to_market_portfolio_value": "108.0",
            }
        ],
    }
    assert results["weekly_dca"] == results["immediate"] | {"number_of_buys": 1}

    daily = results["daily_dca"]
    assert daily | {"purchase_ledger": None} == {
        "number_of_buys": 3,
        "total_contributions": 120.0,
        "quantity_exact": "10.8",
        "cash_balance_exact": "0",
        "fees_paid": 12.0,
        "final_value_exact": "108.0",
        "max_drawdown_raw_portfolio": pytest.approx(8 / 116),
        "max_drawdown_time_weighted": pytest.approx(2 / 29),
        "purchase_ledger": None,
    }
    assert [row["scheduled_at"] for row in daily["purchase_ledger"]] == [
        "2026-01-05T00:00:00+00:00",
        "2026-01-06T00:00:00+00:00",
        "2026-01-07T00:00:00+00:00",
    ]
    assert [row["gross_contribution"] for row in daily["purchase_ledger"]] == [
        "40",
        "40",
        "40",
    ]
    assert [row["fee_paid"] for row in daily["purchase_ledger"]] == ["4.0", "4.0", "4.0"]
    assert [row["quantity"] for row in daily["purchase_ledger"]] == ["3.6", "3.6", "3.6"]


def test_golden_monthly_output_preserves_schedule_ledger_and_drawdown() -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = datetime(2026, 3, 10, tzinfo=UTC)
    candles = constant_candles(start, end)
    plan = InvestmentPlan("120", "1", "0.1", 23)
    events = (CashFlowEvent(start, Decimal("120"), "initial"),)

    result = build_result(
        "MonthlyDCA",
        "BTC/EUR",
        candles,
        events,
        _monthly_deploy(plan, events, candles),
    )

    assert result["number_of_buys"] == 3
    assert result["total_contributions"] == 120.0
    assert result["quantity_exact"] == "10.8"
    assert result["cash_balance_exact"] == "0"
    assert result["fees_paid"] == 12.0
    assert result["final_value_exact"] == "108.0"
    assert result["max_drawdown_raw_portfolio"] == pytest.approx(8 / 116)
    assert result["max_drawdown_time_weighted"] == pytest.approx(2 / 29)
    assert [row["scheduled_at"] for row in result["purchase_ledger"]] == [
        "2026-01-05T00:00:00+00:00",
        "2026-02-05T00:00:00+00:00",
        "2026-03-05T00:00:00+00:00",
    ]
    assert [row["gross_contribution"] for row in result["purchase_ledger"]] == [
        "40",
        "40",
        "40",
    ]


def test_engine_fails_closed_when_purchase_accounting_is_tampered() -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = datetime(2026, 1, 6, tzinfo=UTC)
    candles = constant_candles(start, end)
    plan = InvestmentPlan("120", "1", "0.1", 23)
    events = (CashFlowEvent(start, Decimal("120"), "initial"),)
    purchases = deploy(plan, events, candles, "immediate", WEEKDAYS["monday"])
    purchases[0]["fee_paid"] = Decimal("11")

    with pytest.raises(ValueError, match="purchase ledger accounting invariant failed"):
        build_result("BuyAndHold", "BTC/EUR", candles, events, purchases)
