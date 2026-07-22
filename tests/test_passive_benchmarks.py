from datetime import UTC, datetime

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


def test_legacy_independent_budget_functions_fail_with_migration_message() -> None:
    with pytest.raises(ValueError, match="shared InvestmentPlan"):
        buy_and_hold()
    with pytest.raises(ValueError, match="monthly-budget"):
        dca()


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


def test_one_missing_candle_is_tolerated(tmp_path) -> None:
    complete = prepared_candles(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 20, tzinfo=UTC)
    )
    write_candles(tmp_path, complete[complete["date"] != datetime(2026, 1, 1, 8, tzinfo=UTC)])
    assert len(load_kraken_candles(tmp_path, "BTC/EUR", "4h", "20260101-20260102")) == 5


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


def test_all_deployments_receive_exact_same_plan_cashflows_and_keep_execution_separate(
    tmp_path,
) -> None:
    frame = prepared_candles(
        datetime(2026, 1, 20, tzinfo=UTC), datetime(2026, 2, 4, 20, tzinfo=UTC)
    )
    frame = frame[frame["date"] != datetime(2026, 1, 23, tzinfo=UTC)]
    write_candles(tmp_path, frame)

    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260120-20260205", "200", "40", "0", 23
    )

    assert result["metadata"]["total_contributions"] == 240.0
    assert [event["contributed_at"] for event in result["metadata"]["contribution_schedule"]] == [
        "2026-01-20T00:00:00+00:00",
        "2026-01-23T00:00:00+00:00",
    ]
    assert {row["total_contributions"] for row in result["benchmarks"]} == {240.0}
    assert {row["deployment_method"] for row in result["benchmarks"]} == {
        "immediate",
        "daily_dca",
        "weekly_dca",
    }
    immediate = next(row for row in result["benchmarks"] if row["deployment_method"] == "immediate")
    assert immediate["purchases"][1]["contributed_at"] == "2026-01-23T00:00:00+00:00"
    assert immediate["purchases"][1]["executed_at"] == "2026-01-23T04:00:00+00:00"
    assert immediate["contribution_schedule"] == result["metadata"]["contribution_schedule"]


def test_cli_rejects_removed_independent_contribution_options(monkeypatch, tmp_path) -> None:
    from roundup_crypto_lab import passive_benchmarks

    monkeypatch.setattr(
        "sys.argv",
        [
            "passive_benchmarks",
            "--timerange",
            "20260101-20260102",
            "--output-json",
            str(tmp_path / "result.json"),
            "--daily-contribution",
            "10",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        passive_benchmarks.main()


def test_daily_dca_credits_full_contribution_before_progressive_investment(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 20, tzinfo=UTC), datetime(2026, 1, 22, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260120-20260123", "200", "40", "0", 23
    )
    daily = next(row for row in result["benchmarks"] if row["deployment_method"] == "daily_dca")
    first = daily["equity_curve"][0]
    assert first["cumulative_contributions"] == 200.0
    assert 0 < first["capital_invested"] < 200.0
    assert first["cash_balance"] > 0
    assert first["portfolio_value"] == 200.0
    assert first["net_value"] == 0.0
    assert daily["max_drawdown_time_weighted"] == 0.0


def test_daily_dca_charges_fee_only_on_executed_crypto_purchase(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 20, tzinfo=UTC), datetime(2026, 1, 21, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260120-20260122", "200", "40", "0.1", 23
    )
    daily = next(row for row in result["benchmarks"] if row["deployment_method"] == "daily_dca")
    first = daily["equity_curve"][0]
    assert first["portfolio_value"] == 190.0
    assert first["cumulative_fees_paid"] == 10.0
    assert first["cash_balance"] == 100.0


def test_weekly_dca_keeps_cash_when_no_deployment_day_is_available(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 2, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260102-20260103", "200", "40", "0", 23
    )
    weekly = next(row for row in result["benchmarks"] if row["deployment_method"] == "weekly_dca")
    assert weekly["number_of_buys"] == 0
    assert weekly["capital_invested"] == 0.0
    assert weekly["cash_balance"] == weekly["total_contributions"] == 200.0
    assert weekly["final_value"] == 200.0


def test_same_timestamp_credits_all_cashflows_before_immediate_purchase(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 23, tzinfo=UTC), datetime(2026, 1, 23, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260123-20260124", "200", "40", "0", 23
    )
    immediate = next(row for row in result["benchmarks"] if row["deployment_method"] == "immediate")
    first = immediate["equity_curve"][0]
    assert first["cumulative_contributions"] == 240.0
    assert first["capital_invested"] == 240.0
    assert first["cash_balance"] == 0.0
    assert first["portfolio_value"] == 240.0


@pytest.mark.parametrize("option", ["--daily-contribution=10", "--weekly-contribution=10"])
def test_cli_rejects_equals_form_of_removed_contribution_options(
    monkeypatch, tmp_path, option
) -> None:
    from roundup_crypto_lab import passive_benchmarks

    monkeypatch.setattr(
        "sys.argv",
        [
            "passive_benchmarks",
            "--timerange",
            "20260101-20260102",
            "--output-json",
            str(tmp_path / "x.json"),
            option,
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        passive_benchmarks.main()
