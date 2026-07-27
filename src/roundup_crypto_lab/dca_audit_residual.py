"""Deterministic DCA audit API with exact residual-safe cash reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from roundup_crypto_lab import dca_audit_original as _impl
from roundup_crypto_lab.dca_baselines import allocation_schedule
from roundup_crypto_lab.dca_registry import StrategyDefinition
from roundup_crypto_lab.investment_plan import CashFlowEvent

DCA_RESULT_SCHEMA_VERSION = _impl.DCA_RESULT_SCHEMA_VERSION
DECISION_LEDGER_SCHEMA_VERSION = _impl.DECISION_LEDGER_SCHEMA_VERSION
DCA_METRICS_SCHEMA_VERSION = _impl.DCA_METRICS_SCHEMA_VERSION
DECISION_LEDGER_FIELDS = _impl.DECISION_LEDGER_FIELDS

state_digest = _impl.state_digest
validate_decision_ledger = _impl.validate_decision_ledger
apply_monthly_dca_reference = _impl.apply_monthly_dca_reference
write_dca_audit_csvs = _impl.write_dca_audit_csvs

# Equal Decimal slices can leave a harmless -1E-26 residue when replayed one by
# one. This is below the engine's accounting boundary and must canonicalize to
# zero, while any material overspend still fails closed.
_ACCOUNTING_EPSILON = Decimal("1e-24")


def _balance(value: Decimal, message: str) -> Decimal:
    if not value.is_finite() or value < -_ACCOUNTING_EPSILON:
        raise ValueError(message)
    return Decimal("0") if abs(value) <= _ACCOUNTING_EPSILON else value


def _pending_buckets_before(
    events: Sequence[CashFlowEvent],
    purchases: Sequence[Mapping[str, Any]],
    at: datetime,
    decision_key: tuple[datetime, datetime],
) -> dict[datetime, Decimal]:
    balances: dict[datetime, Decimal] = {}
    for event in events:
        contributed_at = event.contributed_at.astimezone(UTC)
        if contributed_at <= at:
            balances[contributed_at] = balances.get(contributed_at, Decimal("0")) + event.amount
    for purchase in purchases:
        executed_at, scheduled_at, contributed_at = _impl._execution_sort_key(purchase)
        include = executed_at < at or (
            executed_at == at and (scheduled_at, contributed_at) < decision_key
        )
        if not include:
            continue
        if contributed_at not in balances:
            raise ValueError("purchase refers to an unavailable contribution bucket")
        balances[contributed_at] = _balance(
            balances[contributed_at]
            - _impl._decimal(
                purchase["gross_contribution"], "purchase gross contribution"
            ),
            "purchase history overspends a contribution bucket",
        )
    return {timestamp: amount for timestamp, amount in balances.items() if amount > 0}


def build_baseline_decision_ledger(
    *,
    definition: StrategyDefinition,
    events: Sequence[CashFlowEvent],
    candles: pd.DataFrame,
    purchases: Sequence[Mapping[str, Any]],
    parameter_overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct every funding, decision, deferral and execution transition."""
    if not isinstance(definition, StrategyDefinition):
        raise TypeError("definition must be a StrategyDefinition")
    events = tuple(events)
    if any(not isinstance(event, CashFlowEvent) for event in events):
        raise TypeError("events must contain CashFlowEvent values")
    purchases = tuple(sorted(purchases, key=_impl._execution_sort_key))
    allocations = allocation_schedule(
        definition,
        events,
        candles,
        parameter_overrides=parameter_overrides,
    )
    by_key: dict[tuple[datetime, datetime], Mapping[str, Any]] = {}
    for purchase in purchases:
        key = _impl._purchase_key(purchase)
        if key in by_key:
            raise ValueError("duplicate purchase execution for one DCA decision")
        by_key[key] = purchase

    records = [
        _impl._record(
            definition,
            "contribution_event",
            event.contributed_at,
            contributed_at=event.contributed_at.astimezone(UTC).isoformat(),
            event_amount=_impl._canonical_decimal(event.amount, "contribution amount"),
            decision_tag=f"contribution.{event.kind}",
        )
        for event in sorted(events, key=lambda item: (item.contributed_at, item.kind))
    ]

    for allocation in allocations:
        contributed_at = allocation.contributed_at.astimezone(UTC)
        decision_at = allocation.scheduled_at.astimezone(UTC)
        purchase = by_key.pop((contributed_at, decision_at), None)
        pending = _pending_buckets_before(
            events,
            purchases,
            decision_at,
            (decision_at, contributed_at),
        )
        oldest_age = max(
            (int((decision_at - timestamp).total_seconds()) for timestamp in pending),
            default=0,
        )
        requested = _impl._decimal(allocation.gross_amount, "scheduled gross amount")
        executed = (
            Decimal("0")
            if purchase is None
            else _impl._decimal(purchase["gross_contribution"], "executed gross amount")
        )
        if executed - requested > _ACCOUNTING_EPSILON:
            raise ValueError("baseline execution exceeds the scheduled allocation")
        executed_at = (
            None
            if purchase is None
            else _impl._timestamp(purchase["executed_at"], "purchase executed_at")
        )
        common = {
            "contributed_at": contributed_at.isoformat(),
            "decision_at": decision_at.isoformat(),
            "executed_at": "" if executed_at is None else executed_at.isoformat(),
            "execution_candle_open": ""
            if purchase is None
            else _impl._canonical_decimal(purchase["execution_price"], "execution price"),
            "requested_gross_amount": _impl._canonical_decimal(
                requested, "requested gross"
            ),
            "decision_tag": f"{definition.implementation}.scheduled-buy",
            "oldest_pending_cash_age_seconds": oldest_age,
            "purchased_quantity": "0"
            if purchase is None
            else _impl._canonical_decimal(purchase["quantity"], "purchase quantity"),
            "fee_paid": "0"
            if purchase is None
            else _impl._canonical_decimal(purchase["fee_paid"], "purchase fee"),
            "deferral_seconds": 0
            if executed_at is None
            else int((executed_at - decision_at).total_seconds()),
        }
        records.append(
            _impl._record(
                definition,
                "strategy_decision",
                decision_at,
                executed_gross_amount=_impl._canonical_decimal(
                    executed, "executed gross"
                ),
                skip_or_deferral_reason="no_eligible_execution_candle"
                if purchase is None
                else "",
                **common,
            )
        )
        if executed_at is not None and executed_at > decision_at:
            records.append(
                _impl._record(
                    definition,
                    "execution_deferral",
                    decision_at,
                    skip_or_deferral_reason="first_eligible_candle_after_decision",
                    **common,
                )
            )
        if purchase is not None:
            records.append(
                _impl._record(
                    definition,
                    "purchase_execution",
                    executed_at,
                    executed_gross_amount=_impl._canonical_decimal(
                        executed, "executed gross"
                    ),
                    **common,
                )
            )
    if by_key:
        raise ValueError("purchase ledger contains executions without a strategy decision")

    records.sort(key=_impl._ledger_sort_key)
    running_cash = Decimal("0")
    for record in records:
        record["available_cash_before"] = _impl._canonical_decimal(
            running_cash, "cash before ledger record"
        )
        if record["record_type"] == "contribution_event":
            running_cash += _impl._decimal(record["event_amount"], "event amount")
        elif record["record_type"] == "purchase_execution":
            running_cash -= _impl._decimal(
                record["executed_gross_amount"], "executed gross"
            )
        running_cash = _balance(running_cash, "decision ledger produces negative cash")
        record["cash_balance_after_record"] = _impl._canonical_decimal(
            running_cash, "cash after ledger record"
        )
    for index, record in enumerate(records):
        record["record_id"] = (
            f"{definition.strategy_id}:{index:06d}:{record['record_type']}"
        )
        records[index] = {field: record[field] for field in DECISION_LEDGER_FIELDS}
    validate_decision_ledger(records)
    return records


def build_dca_metrics(
    *,
    result: Mapping[str, Any],
    events: Sequence[CashFlowEvent],
    decision_ledger: Sequence[Mapping[str, Any]],
    period_end: datetime,
) -> dict[str, Any]:
    """Build exact deployment, cash-age and decision-count metrics."""
    period_end = _impl._timestamp(period_end, "period end")
    validate_decision_ledger(list(decision_ledger))
    total_contributions = sum((event.amount for event in events), Decimal("0"))
    quantity = _impl._decimal(result["quantity_exact"], "final crypto quantity")
    final_cash = _impl._decimal(result["cash_balance_exact"], "final cash")
    average_cash, maximum_cash, deployment_ratio = _impl._time_weighted_curve_metrics(
        result["equity_curve"], period_end
    )
    execution_rows = [
        row for row in decision_ledger if row["record_type"] == "purchase_execution"
    ]
    decision_rows = [
        row for row in decision_ledger if row["record_type"] == "strategy_decision"
    ]
    gross_total = sum(
        (
            _impl._decimal(row["executed_gross_amount"], "executed gross")
            for row in execution_rows
        ),
        Decimal("0"),
    )
    delays = [
        int(
            (
                _impl._timestamp(row["executed_at"], "execution timestamp")
                - _impl._timestamp(row["contributed_at"], "contribution timestamp")
            ).total_seconds()
        )
        for row in execution_rows
    ]
    delay_weight = sum(
        (
            _impl._decimal(row["executed_gross_amount"], "executed gross")
            * Decimal(delay)
            for row, delay in zip(execution_rows, delays, strict=True)
        ),
        Decimal("0"),
    )
    average_delay = Decimal("0") if gross_total == 0 else delay_weight / gross_total

    remaining: dict[datetime, Decimal] = {}
    for event in events:
        contributed_at = event.contributed_at.astimezone(UTC)
        remaining[contributed_at] = remaining.get(contributed_at, Decimal("0")) + event.amount
    for row in execution_rows:
        contributed_at = _impl._timestamp(row["contributed_at"], "ledger contributed_at")
        if contributed_at not in remaining:
            raise ValueError("decision ledger execution references an unknown contribution")
        remaining[contributed_at] = _balance(
            remaining[contributed_at]
            - _impl._decimal(row["executed_gross_amount"], "executed gross"),
            "decision ledger overspends a contribution bucket",
        )
    oldest_age = max(
        (
            int((period_end - contributed_at).total_seconds())
            for contributed_at, amount in remaining.items()
            if amount > _ACCOUNTING_EPSILON
        ),
        default=0,
    )
    if total_contributions <= 0:
        raise ValueError("DCA metrics require positive total contributions")
    deployed_within = {
        days: sum(
            (
                _impl._decimal(row["executed_gross_amount"], "executed gross")
                for row, delay in zip(execution_rows, delays, strict=True)
                if delay <= days * 24 * 60 * 60
            ),
            Decimal("0"),
        )
        for days in (7, 30, 90, 180)
    }
    classification = (
        "high-deployment"
        if deployment_ratio >= Decimal("0.9")
        else "partial-deployment"
        if deployment_ratio >= Decimal("0.5")
        else "cash-heavy"
    )
    metrics = {
        "schema_version": DCA_METRICS_SCHEMA_VERSION,
        "final_crypto_quantity": _impl._canonical_decimal(quantity, "final quantity"),
        "crypto_quantity_per_unit_contributed": _impl._canonical_decimal(
            quantity / total_contributions, "quantity per contribution"
        ),
        "average_uninvested_cash": _impl._canonical_decimal(
            average_cash, "average cash"
        ),
        "maximum_uninvested_cash": _impl._canonical_decimal(
            maximum_cash, "maximum cash"
        ),
        "time_weighted_capital_deployment_ratio": _impl._canonical_decimal(
            deployment_ratio, "deployment ratio"
        ),
        "average_contribution_to_purchase_delay_seconds": _impl._canonical_decimal(
            average_delay, "average purchase delay"
        ),
        "maximum_contribution_to_purchase_delay_seconds": max(delays, default=0),
        "oldest_uninvested_cash_age_seconds": oldest_age,
        **{
            f"contributions_deployed_within_{days}_days": _impl._canonical_decimal(
                deployed_within[days] / total_contributions,
                f"{days}-day deployment",
            )
            for days in (7, 30, 90, 180)
        },
        "decision_count": len(decision_rows),
        "buy_count": len(execution_rows),
        "no_buy_count": sum(
            _impl._decimal(row["executed_gross_amount"], "executed gross") == 0
            for row in decision_rows
        ),
        "final_uninvested_cash": _impl._canonical_decimal(final_cash, "final cash"),
        "deployment_classification": classification,
        "final_value_difference_vs_monthly_dca": None,
    }
    for field in (
        "time_weighted_capital_deployment_ratio",
        "contributions_deployed_within_7_days",
        "contributions_deployed_within_30_days",
        "contributions_deployed_within_90_days",
        "contributions_deployed_within_180_days",
    ):
        if not Decimal("0") <= _impl._decimal(metrics[field], field) <= Decimal("1"):
            raise ValueError(f"{field} must be between zero and one")
    if len(execution_rows) != result["number_of_buys"]:
        raise ValueError("decision ledger buy count differs from the economic result")
    if abs(gross_total + final_cash - total_contributions) > Decimal("1e-18"):
        raise ValueError("DCA metrics do not reconcile contributions, purchases and cash")
    return metrics


def enrich_dca_strategy_result(
    *,
    result: dict[str, Any],
    definition: StrategyDefinition,
    events: Sequence[CashFlowEvent],
    candles: pd.DataFrame,
    purchases: Sequence[Mapping[str, Any]],
    period_end: datetime,
    parameter_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach the versioned decision ledger and DCA performance metric block."""
    ledger = build_baseline_decision_ledger(
        definition=definition,
        events=events,
        candles=candles,
        purchases=purchases,
        parameter_overrides=parameter_overrides,
    )
    result["dca_strategy_result_schema_version"] = DCA_RESULT_SCHEMA_VERSION
    result["decision_ledger_schema_version"] = DECISION_LEDGER_SCHEMA_VERSION
    result["decision_ledger"] = ledger
    result["dca_metrics"] = build_dca_metrics(
        result=result,
        events=events,
        decision_ledger=ledger,
        period_end=period_end,
    )
    return result
