import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from roundup_crypto_lab.active_backtests import (
    Candle,
    CapitalMode,
    StrategyDecision,
    run_active_backtest,
)
from roundup_crypto_lab.active_builder import build_active_result
from roundup_crypto_lab.active_result_validation import validate_active_result
from roundup_crypto_lab.cash_flow_metrics import build_cash_flow_metrics, dated_irr
from roundup_crypto_lab.investment_plan import InvestmentPlan
from roundup_crypto_lab.passive_cash_flow_reporting import enrich_passive_result

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 3, 1, tzinfo=UTC)


def contribution(timestamp: datetime, amount: str) -> dict[str, object]:
    return {"timestamp": timestamp, "amount": amount}


def snapshot(
    timestamp: datetime, *, equity: str, cash: str, asset: str, share: str
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "equity": equity,
        "cash": cash,
        "asset_value": asset,
        "share_value": share,
    }


def metrics(
    contributions: list[dict[str, object]],
    snapshots: list[dict[str, object]],
    *,
    fee: str = "0",
    total_fees: str = "0",
) -> dict[str, object]:
    return build_cash_flow_metrics(
        initial_capital="100",
        monthly_budget="40",
        fee_ratio=fee,
        contributions=contributions,
        snapshots=snapshots,
        total_fees=total_fees,
        period_end=END,
    )


def test_all_cash_contributions_have_zero_skill_and_risk_metrics() -> None:
    result = metrics(
        [
            contribution(START, "100"),
            contribution(datetime(2026, 2, 1, tzinfo=UTC), "40"),
        ],
        [
            snapshot(START, equity="100", cash="100", asset="0", share="1"),
            snapshot(
                datetime(2026, 2, 1, tzinfo=UTC),
                equity="140",
                cash="140",
                asset="0",
                share="1",
            ),
            snapshot(
                END - timedelta(hours=4),
                equity="140",
                cash="140",
                asset="0",
                share="1",
            ),
        ],
    )

    assert result["time_weighted_return"] == "0"
    assert result["money_weighted_return"] == "0"
    assert result["money_weighted_return_status"] == "converged"
    assert result["max_drawdown_time_weighted"] == "0"
    assert result["max_drawdown_raw_portfolio"] == "0"
    assert result["average_capital_deployed"] == "0"
    assert result["capital_utilization_ratio"] == "0"


def test_constant_price_zero_fee_is_flat_and_fully_utilized() -> None:
    result = metrics(
        [contribution(START, "100")],
        [
            snapshot(START, equity="100", cash="0", asset="100", share="1"),
            snapshot(
                END - timedelta(hours=4),
                equity="100",
                cash="0",
                asset="100",
                share="1",
            ),
        ],
    )

    assert result["profit_abs"] == "0"
    assert result["time_weighted_return"] == "0"
    assert result["capital_utilization_ratio"] == "1"
    assert result["average_capital_deployed"] == "100"


def test_constant_price_fees_are_losses_and_terminal_exit_fee_is_explicit() -> None:
    result = metrics(
        [contribution(START, "100")],
        [
            snapshot(START, equity="99", cash="0", asset="99", share="0.99"),
            snapshot(
                END - timedelta(hours=4),
                equity="99",
                cash="0",
                asset="99",
                share="0.99",
            ),
        ],
        fee="0.01",
        total_fees="1",
    )

    assert result["total_fees"] == "1"
    assert result["profit_abs"] == "-1"
    assert result["time_weighted_return"] == "-0.01"
    assert result["terminal_liquidation_value"] == "98.01"
    assert Decimal(str(result["money_weighted_return"])) < Decimal("-0.01")


def test_contribution_does_not_change_twr_or_time_weighted_drawdown() -> None:
    without_midway = metrics(
        [contribution(START, "100")],
        [
            snapshot(START, equity="100", cash="0", asset="100", share="1"),
            snapshot(
                datetime(2026, 2, 1, tzinfo=UTC),
                equity="110",
                cash="0",
                asset="110",
                share="1.10",
            ),
            snapshot(
                END - timedelta(hours=4),
                equity="99",
                cash="0",
                asset="99",
                share="0.99",
            ),
        ],
    )
    with_midway = metrics(
        [
            contribution(START, "100"),
            contribution(datetime(2026, 2, 1, tzinfo=UTC), "40"),
        ],
        [
            snapshot(START, equity="100", cash="0", asset="100", share="1"),
            snapshot(
                datetime(2026, 2, 1, tzinfo=UTC),
                equity="150",
                cash="40",
                asset="110",
                share="1.10",
            ),
            snapshot(
                END - timedelta(hours=4),
                equity="139",
                cash="40",
                asset="99",
                share="0.99",
            ),
        ],
    )

    assert with_midway["time_weighted_return"] == without_midway[
        "time_weighted_return"
    ]
    assert with_midway["max_drawdown_time_weighted"] == without_midway[
        "max_drawdown_time_weighted"
    ]
    assert with_midway["max_drawdown_raw_portfolio"] != without_midway[
        "max_drawdown_raw_portfolio"
    ]


@pytest.mark.parametrize(
    ("cash_flows", "status"),
    [
        ([(START, Decimal("100"))], "undefined_no_negative_cash_flow"),
        ([(START, Decimal("-100"))], "undefined_no_positive_cash_flow"),
    ],
)
def test_xirr_undefined_cases_are_explicit(
    cash_flows: list[tuple[datetime, Decimal]], status: str
) -> None:
    value, actual_status = dated_irr(cash_flows)
    assert value is None
    assert actual_status == status


def test_xirr_known_answer_and_irregular_dates() -> None:
    one_year = START + timedelta(seconds=31_557_600)
    value, status = dated_irr([(START, Decimal("-100")), (one_year, Decimal("110"))])
    assert status == "converged"
    assert value is not None
    assert abs(value - Decimal("0.10")) < Decimal("1e-10")

    irregular, irregular_status = dated_irr(
        [
            (START, Decimal("-100")),
            (START + timedelta(days=37), Decimal("-40")),
            (START + timedelta(days=173), Decimal("155")),
        ]
    )
    assert irregular_status == "converged"
    assert irregular is not None and irregular.is_finite()


def test_active_all_cash_artifact_exposes_and_validates_common_schema() -> None:
    candles = [
        Candle(START, Decimal("100"), Decimal("100")),
        Candle(datetime(2026, 2, 1, tzinfo=UTC), Decimal("100"), Decimal("100")),
        Candle(END - timedelta(hours=4), Decimal("100"), Decimal("100")),
    ]
    raw = run_active_backtest(
        candles,
        InvestmentPlan("100", "40", "0", 1),
        START,
        END,
        lambda wallet: StrategyDecision(),
        mode=CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS,
    )
    raw["execution_scope"] = {
        "selected_pair": "BTC/EUR",
        "config_digest": "fixture-digest",
        "timeframe": "4h",
        "generated_config": "fixture.json",
    }
    raw["investment_plan"] = {
        "initial_capital": Decimal("100"),
        "monthly_budget": Decimal("40"),
        "fee_ratio": Decimal("0"),
        "contribution_day": 1,
    }
    artifact = build_active_result(
        raw,
        strategy="FixtureStrategy",
        pair="BTC/EUR",
        timeframe="4h",
        timerange="20260101-20260301",
        execution_model="fixture-v1",
        effective_settings={
            "fee_ratio": "0",
            "tradable_balance_ratio": "1",
            "stake_amount": "unlimited",
            "stoploss": "-0.12",
        },
    )
    validate_active_result(artifact, strategy="FixtureStrategy")
    assert artifact["cash_flow_metrics"]["money_weighted_return"] == "0"


def test_passive_enrichment_uses_common_schema_and_no_nonfinite_json() -> None:
    result = {
        "metadata": {
            "timerange": "20260101-20260102",
            "initial_capital": 100.0,
            "monthly_budget": 40.0,
            "fee": 0.0,
            "contribution_schedule": [
                {"contributed_at": START.isoformat(), "amount": 100.0, "kind": "initial"}
            ],
        },
        "benchmarks": [
            {
                "benchmark": "BuyAndHold",
                "pair": "BTC/EUR",
                "number_of_buys": 1,
                "fees_paid": 0.0,
                "profit_total_abs": 0.0,
                "equity_curve": [
                    {
                        "timestamp": START.isoformat(),
                        "cash_balance": 0.0,
                        "crypto_value": 100.0,
                        "portfolio_value": 100.0,
                        "time_weighted_share_value": 1.0,
                    },
                    {
                        "timestamp": (START + timedelta(hours=20)).isoformat(),
                        "cash_balance": 0.0,
                        "crypto_value": 100.0,
                        "portfolio_value": 100.0,
                        "time_weighted_share_value": 1.0,
                    },
                ],
            }
        ],
    }

    enriched = enrich_passive_result(result)
    block = enriched["benchmarks"][0]["cash_flow_metrics"]
    assert enriched["schema_version"] == "passive-benchmarks/v2"
    assert block["schema_version"] == "cash-flow-metrics/v1"
    assert block["capital_utilization_ratio"] == "1"
    json.dumps(enriched, allow_nan=False)
