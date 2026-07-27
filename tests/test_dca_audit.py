from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from roundup_crypto_lab.dca_audit import (
    DCA_METRICS_SCHEMA_VERSION,
    DCA_RESULT_SCHEMA_VERSION,
    DECISION_LEDGER_SCHEMA_VERSION,
    apply_monthly_dca_reference,
    enrich_dca_strategy_result,
    validate_decision_ledger,
    write_dca_audit_csvs,
)
from roundup_crypto_lab.dca_baselines import deploy_registered_baseline
from roundup_crypto_lab.dca_registry import MinimumOrderRule, StrategyDefinition
from roundup_crypto_lab.deployment_engine import build_result
from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan


def candles(start: datetime, end: datetime, *, missing: set[datetime] | None = None):
    missing = set() if missing is None else missing
    rows = []
    for timestamp in pd.date_range(start, end, freq="4h"):
        at = timestamp.to_pydatetime()
        if at in missing:
            continue
        rows.append((at, 10, 10, 10, 10, 1))
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def definition(implementation: str, *, weekday: int | None = None) -> StrategyDefinition:
    parameters = {} if weekday is None else {"weekday": weekday}
    return StrategyDefinition(
        strategy_id={
            "fixed_immediate": "buy-and-hold",
            "fixed_daily": "daily-dca",
            "fixed_weekly": "weekly-dca",
            "fixed_monthly": "monthly-dca",
        }[implementation],
        implementation=implementation,
        implementation_identity=f"roundup_crypto_lab.dca.{implementation}@1",
        strategy_version="1",
        hypothesis="Deterministic audit fixture.",
        decision_cadence={
            "fixed_immediate": "funding_event",
            "fixed_daily": "daily",
            "fixed_weekly": "weekly",
            "fixed_monthly": "monthly",
        }[implementation],
        required_indicators=(),
        parameters=parameters,
        maximum_pending_cash_age_days=None,
        minimum_order=MinimumOrderRule("0", "skip"),
        research_status="baseline",
    )


def audited_result(
    strategy: StrategyDefinition,
    events: tuple[CashFlowEvent, ...],
    frame: pd.DataFrame,
    *,
    period_end: datetime,
    weekday: int | None = None,
):
    plan = InvestmentPlan("100", "40", "0", 1)
    overrides = None if weekday is None else {"weekday": weekday}
    purchases = deploy_registered_baseline(
        plan,
        events,
        frame,
        strategy,
        parameter_overrides=overrides,
    )
    result = build_result(
        strategy.strategy_id,
        "BTC/EUR",
        frame,
        events,
        purchases,
    )
    enrich_dca_strategy_result(
        result=result,
        definition=strategy,
        events=events,
        candles=frame,
        purchases=purchases,
        period_end=period_end,
        parameter_overrides=overrides,
    )
    return result


def test_delayed_buy_records_decision_deferral_and_execution() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    frame = candles(start + timedelta(hours=4), end - timedelta(hours=4))
    events = (CashFlowEvent(start, Decimal("100"), "initial"),)

    result = audited_result(
        definition("fixed_immediate"),
        events,
        frame,
        period_end=end,
    )

    assert result["dca_strategy_result_schema_version"] == DCA_RESULT_SCHEMA_VERSION
    assert result["decision_ledger_schema_version"] == DECISION_LEDGER_SCHEMA_VERSION
    types = [row["record_type"] for row in result["decision_ledger"]]
    assert types == [
        "contribution_event",
        "strategy_decision",
        "execution_deferral",
        "purchase_execution",
    ]
    decision = result["decision_ledger"][1]
    execution = result["decision_ledger"][3]
    assert decision["requested_gross_amount"] == "100"
    assert decision["executed_gross_amount"] == "100"
    assert decision["deferral_seconds"] == 4 * 60 * 60
    assert execution["cash_balance_after_record"] == "0"
    assert execution["purchased_quantity"] == "10"
    assert result["dca_metrics"]["buy_count"] == 1
    assert result["dca_metrics"]["no_buy_count"] == 0
    assert result["dca_metrics"]["maximum_contribution_to_purchase_delay_seconds"] == 14400


def test_permanently_retained_cash_is_classified_and_aged() -> None:
    start = datetime(2026, 1, 2, tzinfo=UTC)  # Friday, with no Monday in range.
    end = datetime(2026, 1, 3, tzinfo=UTC)
    frame = candles(start, end - timedelta(hours=4))
    events = (CashFlowEvent(start, Decimal("100"), "initial"),)

    result = audited_result(
        definition("fixed_weekly", weekday=0),
        events,
        frame,
        period_end=end,
        weekday=0,
    )

    metrics = result["dca_metrics"]
    assert metrics["schema_version"] == DCA_METRICS_SCHEMA_VERSION
    assert metrics["decision_count"] == 0
    assert metrics["buy_count"] == 0
    assert metrics["no_buy_count"] == 0
    assert metrics["final_uninvested_cash"] == "100"
    assert metrics["oldest_uninvested_cash_age_seconds"] == 86400
    assert metrics["time_weighted_capital_deployment_ratio"] == "0"
    assert metrics["deployment_classification"] == "cash-heavy"


def test_multiple_funding_buckets_reconcile_exactly() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 4, tzinfo=UTC)
    frame = candles(start, end - timedelta(hours=4))
    events = (
        CashFlowEvent(start, Decimal("100"), "initial"),
        CashFlowEvent(datetime(2026, 1, 2, tzinfo=UTC), Decimal("40"), "monthly"),
    )

    result = audited_result(
        definition("fixed_daily"),
        events,
        frame,
        period_end=end,
    )

    executions = [
        row
        for row in result["decision_ledger"]
        if row["record_type"] == "purchase_execution"
    ]
    assert sum(
        (Decimal(row["executed_gross_amount"]) for row in executions),
        Decimal("0"),
    ) + Decimal(result["cash_balance_exact"]) == Decimal("140")
    assert result["dca_metrics"]["decision_count"] == result["number_of_buys"]
    assert result["dca_metrics"]["buy_count"] == result["number_of_buys"]
    assert result["dca_metrics"]["contributions_deployed_within_7_days"] == "1"
    assert result["dca_metrics"]["oldest_uninvested_cash_age_seconds"] == 0


def test_monthly_reference_is_exact_and_can_be_negative() -> None:
    benchmarks = [
        {
            "benchmark": "DailyDCA",
            "final_value_exact": "95.50",
            "dca_metrics": {"final_value_difference_vs_monthly_dca": None},
        },
        {
            "benchmark": "MonthlyDCA",
            "final_value_exact": "100.00",
            "dca_metrics": {"final_value_difference_vs_monthly_dca": None},
        },
    ]

    apply_monthly_dca_reference(benchmarks)

    assert benchmarks[0]["dca_metrics"]["final_value_difference_vs_monthly_dca"] == "-4.5"
    assert benchmarks[1]["dca_metrics"]["final_value_difference_vs_monthly_dca"] == "0"


def test_ledger_validation_rejects_duplicate_unordered_nonfinite_and_impossible_rows() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    frame = candles(start, end - timedelta(hours=4))
    events = (CashFlowEvent(start, Decimal("100"), "initial"),)
    ledger = audited_result(
        definition("fixed_immediate"),
        events,
        frame,
        period_end=end,
    )["decision_ledger"]

    duplicate = deepcopy(ledger)
    duplicate.append(deepcopy(duplicate[-1]))
    with pytest.raises(ValueError, match="duplicate record ids"):
        validate_decision_ledger(duplicate)

    unordered = deepcopy(ledger)
    unordered[0], unordered[-1] = unordered[-1], unordered[0]
    with pytest.raises(ValueError, match="chronological|cash-before"):
        validate_decision_ledger(unordered)

    nonfinite = deepcopy(ledger)
    nonfinite[0]["event_amount"] = "NaN"
    with pytest.raises(ValueError, match="finite"):
        validate_decision_ledger(nonfinite)

    impossible = deepcopy(ledger)
    impossible[-1]["cash_balance_after_record"] = "999"
    with pytest.raises(ValueError, match="cash-after"):
        validate_decision_ledger(impossible)


def test_flat_csv_artifacts_preserve_exact_ledger_values(tmp_path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    frame = candles(start, end - timedelta(hours=4))
    events = (CashFlowEvent(start, Decimal("100"), "initial"),)
    result = audited_result(
        definition("fixed_immediate"),
        events,
        frame,
        period_end=end,
    )
    result["benchmark"] = "BuyAndHold"
    result["pair"] = "BTC/EUR"
    payload = {"benchmarks": [result]}

    write_dca_audit_csvs(payload, tmp_path)

    ledger = tmp_path / "buy-and-hold-btc-eur-decision-ledger.csv"
    assert ledger.is_file()
    assert "requested_gross_amount" in ledger.read_text(encoding="utf-8").splitlines()[0]
    assert ",100,100," in ledger.read_text(encoding="utf-8")
    assert (tmp_path / "dca-performance-metrics.csv").is_file()
    assert (tmp_path / "dca-comparison.csv").is_file()
