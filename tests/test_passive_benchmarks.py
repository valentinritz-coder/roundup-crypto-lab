from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from scripts.passive_benchmarks import buy_and_hold, dca, parse_timerange


def candles(rows):
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def test_buy_and_hold_uses_first_open_applies_fee_and_drawdown() -> None:
    frame = candles(
        [
            (datetime(2026, 1, 1, tzinfo=UTC), 10, 11, 9, 10, 1),
            (datetime(2026, 1, 1, 4, tzinfo=UTC), 8, 9, 7, 8, 1),
            (datetime(2026, 1, 1, 8, tzinfo=UTC), 12, 13, 11, 12, 1),
        ]
    )
    result = buy_and_hold(frame, "BTC/EUR", Decimal("100"), Decimal("0.1"))
    assert result["quantity"] == 9.0
    assert result["fees_paid"] == 10.0
    assert result["final_value"] == 108.0
    assert result["profit_total"] == 0.08
    assert result["max_drawdown"] == 0.2


def test_daily_dca_defers_to_first_available_candle_and_has_one_buy_per_day() -> None:
    frame = candles(
        [
            (datetime(2026, 1, 1, 4, tzinfo=UTC), 10, 11, 9, 10, 1),
            (datetime(2026, 1, 1, 8, tzinfo=UTC), 10, 11, 9, 10, 1),
            (datetime(2026, 1, 2, 4, tzinfo=UTC), 20, 21, 19, 20, 1),
        ]
    )
    result = dca(frame, "BTC/EUR", Decimal("100"), Decimal("0.1"))
    assert result["number_of_buys"] == 2
    assert [row["executed_at"] for row in result["purchases"]] == [
        "2026-01-01T04:00:00+00:00",
        "2026-01-02T04:00:00+00:00",
    ]
    assert result["fees_paid"] == 20.0
    assert result["quantity"] == 13.5
    assert result["average_entry_price"] == pytest.approx(14.8148148148)
    assert result["total_contributions"] == 200.0
    assert result["final_value"] == 270.0
    assert result["profit_total"] == 0.35
    assert result["max_drawdown_time_weighted"] == 0.0


def test_weekly_dca_only_buys_requested_weekday_across_year_boundary() -> None:
    frame = candles(
        [
            (datetime(2025, 12, 29, 4, tzinfo=UTC), 10, 10, 10, 10, 0),  # Monday
            (datetime(2026, 1, 1, 4, tzinfo=UTC), 11, 11, 11, 11, 0),
            (datetime(2026, 1, 5, 4, tzinfo=UTC), 12, 12, 12, 12, 0),  # Monday
        ]
    )
    result = dca(frame, "ETH/EUR", Decimal("10"), Decimal("0"), weekly_day=0)
    assert result["number_of_buys"] == 2
    assert [row["scheduled_at"][:10] for row in result["purchases"]] == ["2025-12-29", "2026-01-05"]


def test_future_candles_do_not_change_prior_purchase_or_equity() -> None:
    prefix = candles(
        [
            (datetime(2026, 1, 1, tzinfo=UTC), 10, 10, 10, 10, 0),
            (datetime(2026, 1, 2, tzinfo=UTC), 20, 20, 20, 20, 0),
        ]
    )
    extended = pd.concat(
        [prefix, candles([(datetime(2026, 1, 3, tzinfo=UTC), 5, 5, 5, 5, 0)])], ignore_index=True
    )
    before = dca(prefix, "BTC/EUR", Decimal("10"), Decimal("0"))
    after = dca(extended, "BTC/EUR", Decimal("10"), Decimal("0"))
    assert after["purchases"][:2] == before["purchases"]
    assert after["equity_curve"][:2] == before["equity_curve"]


def test_timerange_is_strict() -> None:
    assert parse_timerange("20260101-20260102")
    with pytest.raises(ValueError):
        parse_timerange("20260102-20260102")
