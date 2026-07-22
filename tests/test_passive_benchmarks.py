from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule
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


def test_daily_dca_deploys_full_same_timestamp_bucket(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 23, tzinfo=UTC), datetime(2026, 1, 25, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260123-20260126", "200", "40", "0", 23
    )
    daily = next(row for row in result["benchmarks"] if row["deployment_method"] == "daily_dca")
    assert daily["total_contributions"] == daily["capital_invested"] == 240.0
    assert daily["cash_balance"] == 0.0
    assert daily["number_of_buys"] == 3
    first = daily["equity_curve"][0]
    assert first["cumulative_contributions"] == 240.0
    assert first["capital_invested"] == 80.0
    assert first["cash_balance"] == 160.0
    assert first["portfolio_value"] == 240.0
    assert first["net_value"] == 0.0


def test_weekly_dca_deploys_full_same_timestamp_bucket(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 2, 23, tzinfo=UTC), datetime(2026, 3, 9, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260223-20260310", "200", "40", "0", 23, "monday"
    )
    weekly = next(row for row in result["benchmarks"] if row["deployment_method"] == "weekly_dca")
    assert weekly["total_contributions"] == weekly["capital_invested"] == 240.0
    assert weekly["cash_balance"] == 0.0
    assert weekly["number_of_buys"] == 3
    assert [purchase["gross_contribution"] for purchase in weekly["purchases"]] == [
        80.0,
        80.0,
        80.0,
    ]


def test_deployment_buckets_are_deterministic_when_same_timestamp_event_order_changes() -> None:
    from roundup_crypto_lab.investment_plan import CashFlowEvent
    from roundup_crypto_lab.passive_benchmarks import deployment_buckets

    timestamp = datetime(2026, 1, 23, tzinfo=UTC)
    initial = CashFlowEvent(timestamp, Decimal("200"), "initial")
    monthly = CashFlowEvent(timestamp, Decimal("40"), "monthly")
    assert deployment_buckets((initial, monthly)) == deployment_buckets((monthly, initial))


def benchmark(result, method: str = "immediate") -> dict:
    return next(row for row in result["benchmarks"] if row["deployment_method"] == method)


def test_hand_calculated_fee_fixture_has_exact_auditable_ledger(tmp_path) -> None:
    """100 EUR at 10 with a 10% fee buys 9 units and finishes worth 90 EUR."""
    write_candles(
        tmp_path,
        candles(
            [
                (datetime(2026, 1, 1, hour, tzinfo=UTC), 10, 10, 10, 10, 1)
                for hour in (0, 4, 8, 12, 16, 20)
            ]
        ),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260101-20260102", "100", "1", "0.1", 23
    )
    row = benchmark(result)
    ledger = row["purchase_ledger"]

    assert row["total_contributions"] == 100.0
    assert row["fees_paid"] == 10.0
    assert row["quantity"] == 9.0
    assert Decimal(row["average_entry_price_exact"]) == Decimal("100") / Decimal("9")
    assert row["final_value"] == 90.0
    assert row["profit_total_abs"] == -10.0
    assert row["profit_total"] == -0.1
    assert row["max_drawdown_time_weighted"] == 0.0
    assert ledger == [
        {
            "contributed_at": "2026-01-01T00:00:00+00:00",
            "scheduled_at": "2026-01-01T00:00:00+00:00",
            "executed_at": "2026-01-01T00:00:00+00:00",
            "execution_price": "10",
            "gross_contribution": "100",
            "fee_paid": "10.0",
            "net_contribution": "90.0",
            "quantity": "9.0",
            "cumulative_quantity": "9.0",
            "cumulative_gross_contributions": "100",
            "cumulative_fees": "10.0",
            "residual_cash": "0",
            "marked_to_market_portfolio_value": "90.0",
        }
    ]


@pytest.mark.parametrize("fee", ["0", "0.1"])
def test_constant_price_invariants_and_fee_loss_only(tmp_path, fee: str) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260101-20260103", "100", "40", fee, 2
    )
    for row in result["benchmarks"]:
        total = Decimal(str(row["total_contributions"]))
        final = Decimal(str(row["final_value"]))
        fees = Decimal(str(row["fees_paid"]))
        assert final == total - fees
        assert (
            row["max_drawdown_time_weighted"] == 0.0
            if fee == "0"
            else row["max_drawdown_time_weighted"] >= 0
        )
        assert Decimal(str(row["capital_invested"])) + Decimal(str(row["cash_balance"])) == total
        cumulative_quantity = Decimal("0")
        for entry in row["purchase_ledger"]:
            gross = Decimal(entry["gross_contribution"])
            net = Decimal(entry["net_contribution"])
            charged = Decimal(entry["fee_paid"])
            quantity = Decimal(entry["quantity"])
            assert gross == net + charged
            assert quantity == net / Decimal(entry["execution_price"])
            assert Decimal(entry["cumulative_quantity"]) >= cumulative_quantity
            cumulative_quantity = Decimal(entry["cumulative_quantity"])


def test_contribution_neutral_drawdown_is_not_erased_by_deposit(tmp_path) -> None:
    frame = prepared_candles(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, 20, tzinfo=UTC))
    frame.loc[frame["date"] >= pd.Timestamp("2026-01-02T00:00:00Z"), ["open", "close"]] = 50
    write_candles(tmp_path, frame)
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260101-20260104", "100", "100", "0", 2
    )
    row = benchmark(result)
    jan2 = next(
        point for point in row["equity_curve"] if point["timestamp"].startswith("2026-01-02")
    )
    assert jan2["portfolio_value"] == 150.0
    assert jan2["time_weighted_share_value"] == 0.5
    assert row["max_drawdown_time_weighted"] == 0.5


def test_purchase_ledger_csv_is_written_even_without_buys(tmp_path) -> None:
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 2, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260102-20260103", "100", "40", "0", 23
    )
    from roundup_crypto_lab.passive_benchmarks import write_details

    output = tmp_path / "artifacts"
    write_details(result, output)
    ledger = output / "weekly-dca-btc-eur-purchase-ledger.csv"
    assert ledger.read_text(encoding="utf-8").splitlines() == [
        "contributed_at,scheduled_at,executed_at,execution_price,gross_contribution,fee_paid,"
        "net_contribution,quantity,cumulative_quantity,cumulative_gross_contributions,"
        "cumulative_fees,residual_cash,marked_to_market_portfolio_value"
    ]


@pytest.mark.parametrize(
    ("prices", "expected_profit_sign"),
    [([100, 125, 150], 1), ([150, 125, 100], -1), ([100, 50, 100], 0)],
    ids=["rising", "falling", "v-shaped"],
)
def test_deterministic_synthetic_price_paths(
    tmp_path, prices: list[int], expected_profit_sign: int
) -> None:
    """Small rising, falling, and V-shaped fixtures protect valuation semantics."""
    dates = pd.date_range(datetime(2026, 1, 1, tzinfo=UTC), periods=18, freq="4h")
    frame = candles(
        [
            (at, prices[index // 6], prices[index // 6], prices[index // 6], prices[index // 6], 1)
            for index, at in enumerate(dates)
        ]
    )
    write_candles(tmp_path, frame)
    row = benchmark(
        run_passive_benchmarks(
            tmp_path, ["BTC/EUR"], "4h", "20260101-20260104", "100", "1", "0", 23
        )
    )
    profit = Decimal(str(row["profit_total_abs"]))
    assert profit == 0 if expected_profit_sign == 0 else profit * expected_profit_sign > 0
    assert Decimal(row["final_value_exact"]) == (
        Decimal(row["quantity_exact"]) * Decimal(row["final_price_exact"])
        + Decimal(row["cash_balance_exact"])
    )


def test_investment_plan_total_covers_partial_months_and_clipped_short_month() -> None:
    """The plan, not candle execution, is the source of gross investor contributions."""
    plan = InvestmentPlan("200", "40", "0", 31)
    events = contribution_schedule(
        plan,
        datetime(2026, 1, 30, tzinfo=UTC),
        datetime(2026, 3, 1, tzinfo=UTC),
    )
    assert [(event.contributed_at.date().isoformat(), event.amount) for event in events] == [
        ("2026-01-30", Decimal("200")),
        ("2026-01-31", Decimal("40")),
        ("2026-02-28", Decimal("40")),
    ]
    assert sum((event.amount for event in events), Decimal("0")) == Decimal("280")


@pytest.mark.parametrize(
    ("method", "expected_drawdown"),
    [("immediate", 0.0), ("daily_dca", 1 / 19), ("weekly_dca", 0.0)],
)
def test_constant_price_fee_drawdown_uses_first_post_execution_peak(
    tmp_path, method: str, expected_drawdown: float
) -> None:
    """An initial fee establishes the first observed peak; later fees draw down from it."""
    write_candles(
        tmp_path,
        prepared_candles(datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 6, 20, tzinfo=UTC)),
    )
    result = run_passive_benchmarks(
        tmp_path, ["BTC/EUR"], "4h", "20260105-20260107", "100", "1", "0.1", 23
    )
    row = benchmark(result, method)
    assert row["max_drawdown_time_weighted"] == pytest.approx(expected_drawdown)
