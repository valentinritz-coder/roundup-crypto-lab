from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from roundup_crypto_lab.dca_audit import (
    enrich_dca_strategy_result,
    write_dca_audit_csvs,
)
from roundup_crypto_lab.dca_baselines import deploy_registered_baseline
from roundup_crypto_lab.dca_registry import MinimumOrderRule, StrategyDefinition
from roundup_crypto_lab.deployment_engine import build_result
from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan


def test_daily_dca_csv_export_accepts_sub_quantum_decimal_residue(tmp_path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 4, tzinfo=UTC)
    dates = pd.date_range(start, end - timedelta(hours=4), freq="4h")
    candles = pd.DataFrame(
        {
            "date": dates,
            "open": [Decimal("10")] * len(dates),
            "high": [Decimal("10")] * len(dates),
            "low": [Decimal("10")] * len(dates),
            "close": [Decimal("10")] * len(dates),
            "volume": [Decimal("1")] * len(dates),
        }
    )
    events = (CashFlowEvent(start, Decimal("100"), "initial"),)
    plan = InvestmentPlan("100", "40", "0", 1)
    strategy = StrategyDefinition(
        strategy_id="daily-dca",
        implementation="fixed_daily",
        implementation_identity="roundup_crypto_lab.dca.fixed_daily@1",
        strategy_version="1",
        hypothesis="Exercise recurring audit CSV reconciliation.",
        decision_cadence="daily",
        required_indicators=(),
        parameters={},
        maximum_pending_cash_age_days=None,
        minimum_order=MinimumOrderRule("0", "skip"),
        research_status="baseline",
    )
    purchases = deploy_registered_baseline(plan, events, candles, strategy)
    result = build_result("DailyDCA", "BTC/EUR", candles, events, purchases)
    enrich_dca_strategy_result(
        result=result,
        definition=strategy,
        events=events,
        candles=candles,
        purchases=purchases,
        period_end=end,
    )
    result["benchmark"] = "DailyDCA"
    result["pair"] = "BTC/EUR"

    write_dca_audit_csvs({"benchmarks": [result]}, tmp_path)

    assert len(purchases) == 3
    assert (tmp_path / "daily-dca-btc-eur-decision-ledger.csv").is_file()
    assert (tmp_path / "dca-performance-metrics.csv").is_file()
    assert (tmp_path / "dca-comparison.csv").is_file()
