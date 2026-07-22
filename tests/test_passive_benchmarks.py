from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.passive_benchmarks import (
    buy_and_hold,
    dca,
    load_kraken_candles,
    parse_timerange,
    run_passive_benchmarks,
)


def candles(rows):
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def prepared_candles(start: datetime, end: datetime) -> pd.DataFrame:
    return candles(
        [(timestamp, 100, 101, 99, 100, 1) for timestamp in pd.date_range(start, end, freq="4h")]
    )


def write_candles(tmp_path, frame: pd.DataFrame, pair: str = "BTC/EUR") -> None:
    frame.to_feather(tmp_path / f"{pair.replace('/', '_')}-4h.feather")


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
    assert result["max_drawdown"] == result["max_drawdown_time_weighted"]


def test_dca_fee_uses_net_contribution_for_time_weighted_shares_on_flat_market() -> None:
    frame = candles(
        [
            (datetime(2026, 1, 1, tzinfo=UTC), 100, 100, 100, 100, 0),
            (datetime(2026, 1, 2, tzinfo=UTC), 100, 100, 100, 100, 0),
        ]
    )
    result = dca(frame, "BTC/EUR", Decimal("100"), Decimal("0.1"))
    assert [purchase["net_contribution"] for purchase in result["purchases"]] == [90.0, 90.0]
    assert [row["time_weighted_share_value"] for row in result["equity_curve"]] == [1.0, 1.0]
    assert result["max_drawdown_time_weighted"] == 0.0
    assert result["max_drawdown"] == 0.0
    assert result["max_drawdown_raw_portfolio"] == 0.0
    assert result["profit_total"] == -0.1


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


def test_candle_gap_outside_timerange_does_not_fail(tmp_path) -> None:
    selected = prepared_candles(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 20, tzinfo=UTC)
    )
    historical_gap = candles(
        [
            (datetime(2025, 12, 30, tzinfo=UTC), 100, 101, 99, 100, 1),
            (datetime(2025, 12, 31, 12, tzinfo=UTC), 100, 101, 99, 100, 1),
        ]
    )
    write_candles(tmp_path, pd.concat([historical_gap, selected], ignore_index=True))

    loaded = load_kraken_candles(tmp_path, "BTC/EUR", "4h", "20260101-20260102")

    assert len(loaded) == 6
    assert loaded.iloc[0]["date"] == pd.Timestamp("2026-01-01T00:00:00Z")


def test_candles_without_gap_in_timerange_work_and_report_coverage(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 5, 20, tzinfo=UTC)),
    )

    result = run_passive_benchmarks(tmp_path, ["BTC/EUR"], "4h", "20260105-20260106")

    assert result["metadata"]["pair_candle_coverage"] == {
        "BTC/EUR": {
            "expected_candles": 6,
            "actual_candles": 6,
            "missing_candles_estimate": 0,
            "maximum_gap_hours": 4.0,
        }
    }


def test_one_missing_candle_is_tolerated_and_purchase_is_deferred(tmp_path) -> None:
    complete = prepared_candles(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 20, tzinfo=UTC)
    )
    write_candles(tmp_path, complete[complete["date"] != datetime(2026, 1, 1, 8, tzinfo=UTC)])

    loaded = load_kraken_candles(tmp_path, "BTC/EUR", "4h", "20260101-20260102")

    assert len(loaded) == 5

    frame = candles(
        [
            (datetime(2026, 1, 1, 4, tzinfo=UTC), 10, 10, 10, 10, 1),
            (datetime(2026, 1, 1, 12, tzinfo=UTC), 20, 20, 20, 20, 1),
        ]
    )

    result = dca(frame, "BTC/EUR", Decimal("10"), Decimal("0"))

    assert result["purchases"][0]["scheduled_at"] == "2026-01-01T00:00:00+00:00"
    assert result["purchases"][0]["executed_at"] == "2026-01-01T04:00:00+00:00"


def test_two_consecutive_missing_candles_raise_descriptive_error(tmp_path) -> None:
    frame = prepared_candles(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 20, tzinfo=UTC))
    frame = frame[frame["date"] != datetime(2026, 1, 1, 8, tzinfo=UTC)]
    frame = frame[frame["date"] != datetime(2026, 1, 1, 12, tzinfo=UTC)]
    write_candles(tmp_path, frame)

    with pytest.raises(
        ValueError,
        match=(
            "BTC/EUR: largest gap 0 days 12:00:00 between "
            "2026-01-01T04:00:00\\+00:00 and 2026-01-01T16:00:00\\+00:00"
        ),
    ):
        load_kraken_candles(tmp_path, "BTC/EUR", "4h", "20260101-20260102")


@pytest.mark.parametrize(
    "missing_at", [datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 20, tzinfo=UTC)]
)
def test_timerange_requires_exact_start_and_end_coverage(tmp_path, missing_at: datetime) -> None:
    frame = prepared_candles(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 20, tzinfo=UTC))
    write_candles(tmp_path, frame[frame["date"] != missing_at])

    with pytest.raises(ValueError, match="timerange (start|end)"):
        load_kraken_candles(tmp_path, "BTC/EUR", "4h", "20260101-20260102")


def test_loader_excludes_future_candles_to_prevent_lookahead(tmp_path) -> None:
    frame = prepared_candles(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, 20, tzinfo=UTC))
    write_candles(tmp_path, frame)

    loaded = load_kraken_candles(tmp_path, "BTC/EUR", "4h", "20260101-20260102")

    assert loaded["date"].max() == pd.Timestamp("2026-01-01T20:00:00Z")
