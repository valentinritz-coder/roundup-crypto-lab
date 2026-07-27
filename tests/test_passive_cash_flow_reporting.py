from datetime import UTC, datetime, timedelta
from decimal import Decimal

from roundup_crypto_lab.passive_cash_flow_reporting import enrich_passive_result


START = datetime(2026, 1, 1, tzinfo=UTC)


def test_enrichment_rebuilds_equity_from_serialized_float_components() -> None:
    cash_float = 0.1
    asset_float = 0.2
    independently_serialized_total = 0.30000000000000004
    result = {
        "metadata": {
            "timerange": "20260101-20260102",
            "initial_capital": 0.3,
            "monthly_budget": 0.3,
            "fee": 0.0,
            "contribution_schedule": [
                {"contributed_at": START.isoformat(), "amount": 0.3, "kind": "initial"}
            ],
        },
        "benchmarks": [
            {
                "benchmark": "WeeklyDCA",
                "pair": "ETH/EUR",
                "number_of_buys": 1,
                "fees_paid": 0.0,
                "profit_total_abs": 5.551115123125783e-17,
                "equity_curve": [
                    {
                        "timestamp": START.isoformat(),
                        "cash_balance": cash_float,
                        "crypto_value": asset_float,
                        "portfolio_value": independently_serialized_total,
                        "time_weighted_share_value": 1.0,
                    },
                    {
                        "timestamp": (START + timedelta(hours=20)).isoformat(),
                        "cash_balance": cash_float,
                        "crypto_value": asset_float,
                        "portfolio_value": independently_serialized_total,
                        "time_weighted_share_value": 1.0,
                    },
                ],
            }
        ],
    }

    serialized_total = Decimal(str(independently_serialized_total))
    serialized_components = Decimal(str(cash_float)) + Decimal(str(asset_float))
    assert serialized_total != serialized_components

    enriched = enrich_passive_result(result)
    metrics = enriched["benchmarks"][0]["cash_flow_metrics"]

    assert Decimal(metrics["final_value"]) == serialized_components
    assert Decimal(metrics["profit_abs"]) == Decimal("0")
