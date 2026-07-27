"""Deterministic decision ledgers and DCA-specific deployment metrics."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.dca_baselines import allocation_schedule
from roundup_crypto_lab.dca_registry import StrategyDefinition
from roundup_crypto_lab.investment_plan import CashFlowEvent

DCA_RESULT_SCHEMA_VERSION = "dca-strategy-result/v1"
DECISION_LEDGER_SCHEMA_VERSION = "dca-decision-ledger/v1"
DCA_METRICS_SCHEMA_VERSION = "dca-performance-metrics/v1"

_RECORD_TYPES = frozenset(
    {
        "contribution_event",
        "strategy_decision",
        "execution_deferral",
        "purchase_execution",
    }
)

DECISION_LEDGER_FIELDS = (
    "record_id",
    "record_type",
    "strategy_id",
    "strategy_version",
    "timestamp",
    "contributed_at",
    "decision_at",
    "executed_at",
    "execution_candle_open",
    "available_cash_before",
    "event_amount",
    "requested_gross_amount",
    "executed_gross_amount",
    "decision_tag",
    "indicator_values",
    "oldest_pending_cash_age_seconds",
    "cash_balance_after_record",
    "purchased_quantity",
    "fee_paid",
    "skip_or_deferral_reason",
    "deferral_seconds",
    "state_digest_before",
    "state_digest_after",
)

_EMPTY_STATE_DIGEST = "sha256:" + hashlib.sha256(b"{}").hexdigest()


def _decimal(value: object, name: str, *, allow_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be boolean")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not number.is_finite() or (not allow_negative and number < 0):
        qualifier = "finite" if allow_negative else "finite and non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return number


def _canonical_decimal(value: object, name: str, *, allow_negative: bool = False) -> str:
    number = _decimal(value, name, allow_negative=allow_negative)
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError(f"{name} must be a datetime or ISO-8601 timestamp")
    if result.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result.astimezone(UTC)


def _json_value(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{name} keys must be strings")
        return {key: _json_value(value[key], f"{name}.{key}") for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item, f"{name}[]") for item in value]
    if isinstance(value, Decimal):
        return _canonical_decimal(value, name, allow_negative=True)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError(f"{name} must contain deterministic finite JSON-compatible values")


def _json_text(value: object, name: str) -> str:
    try:
        return json.dumps(
            _json_value(value, name),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be deterministic finite JSON") from exc


def state_digest(value: Mapping[str, Any]) -> str:
    """Return a stable digest for a strategy state mapping."""
    if not isinstance(value, Mapping):
        raise TypeError("strategy state must be a mapping")
    return "sha256:" + hashlib.sha256(
        _json_text(dict(value), "strategy state").encode("utf-8")
    ).hexdigest()


def _purchase_key(purchase: Mapping[str, Any]) -> tuple[datetime, datetime]:
    return (
        _timestamp(purchase["contributed_at"], "purchase contributed_at"),
        _timestamp(purchase["scheduled_at"], "purchase scheduled_at"),
    )


def _execution_sort_key(purchase: Mapping[str, Any]) -> tuple[datetime, datetime, datetime]:
    return (
        _timestamp(purchase["executed_at"], "purchase executed_at"),
        _timestamp(purchase["scheduled_at"], "purchase scheduled_at"),
        _timestamp(purchase["contributed_at"], "purchase contributed_at"),
    )


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
        executed_at, scheduled_at, contributed_at = _execution_sort_key(purchase)
        include = executed_at < at or (
            executed_at == at and (scheduled_at, contributed_at) < decision_key
        )
        if not include:
            continue
        if contributed_at not in balances:
            raise ValueError("purchase refers to an unavailable contribution bucket")
        balances[contributed_at] -= _decimal(
            purchase["gross_contribution"], "purchase gross contribution"
        )
        if balances[contributed_at] < 0:
            raise ValueError("purchase history overspends a contribution bucket")
    return {timestamp: amount for timestamp, amount in balances.items() if amount > 0}


def _record(
    definition: StrategyDefinition,
    record_type: str,
    timestamp: datetime,
    **values: Any,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "record_id": "",
        "record_type": record_type,
        "strategy_id": definition.strategy_id,
        "strategy_version": definition.strategy_version,
        "timestamp": timestamp.astimezone(UTC).isoformat(),
        "contributed_at": "",
        "decision_at": "",
        "executed_at": "",
        "execution_candle_open": "",
        "available_cash_before": "0",
        "event_amount": "0",
        "requested_gross_amount": "0",
        "executed_gross_amount": "0",
        "decision_tag": "",
        "indicator_values": "{}",
        "oldest_pending_cash_age_seconds": 0,
        "cash_balance_after_record": "0",
        "purchased_quantity": "0",
        "fee_paid": "0",
        "skip_or_deferral_reason": "",
        "deferral_seconds": 0,
        "state_digest_before": _EMPTY_STATE_DIGEST,
        "state_digest_after": _EMPTY_STATE_DIGEST,
    }
    defaults.update(values)
    return defaults


def _ledger_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    timestamp = _timestamp(row["timestamp"], "ledger timestamp")
    record_type = row["record_type"]
    if record_type == "contribution_event":
        priority = 0
    elif record_type == "purchase_execution":
        decision_at = _timestamp(row["decision_at"], "ledger decision_at")
        priority = 1 if decision_at < timestamp else 4
    elif record_type == "strategy_decision":
        priority = 2
    else:
        priority = 3
    return (timestamp, priority, row["decision_at"], row["contributed_at"])


def build_baseline_decision_ledger(
    *,
    definition: StrategyDefinition,
    events: Sequence[CashFlowEvent],
    candles: pd.DataFrame,
    purchases: Sequence[Mapping[str, Any]],
    parameter_overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct every fixed-baseline funding, decision and execution transition."""
    if not isinstance(definition, StrategyDefinition):
        raise TypeError("definition must be a StrategyDefinition")
    events = tuple(events)
    if any(not isinstance(event, CashFlowEvent) for event in events):
        raise TypeError("events must contain CashFlowEvent values")
    purchases = tuple(sorted(purchases, key=_execution_sort_key))
    allocations = allocation_schedule(
        definition,
        events,
        candles,
        parameter_overrides=parameter_overrides,
    )
    by_key: dict[tuple[datetime, datetime], Mapping[str, Any]] = {}
    for purchase in purchases:
        key = _purchase_key(purchase)
        if key in by_key:
            raise ValueError("duplicate purchase execution for one DCA decision")
        by_key[key] = purchase

    records = [
        _record(
            definition,
            "contribution_event",
            event.contributed_at,
            contributed_at=event.contributed_at.astimezone(UTC).isoformat(),
            event_amount=_canonical_decimal(event.amount, "contribution amount"),
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
        requested = _decimal(allocation.gross_amount, "scheduled gross amount")
        executed = (
            Decimal("0")
            if purchase is None
            else _decimal(purchase["gross_contribution"], "executed gross amount")
        )
        if executed > requested:
            raise ValueError("baseline execution exceeds the scheduled allocation")
        executed_at = (
            None
            if purchase is None
            else _timestamp(purchase["executed_at"], "purchase executed_at")
        )
        common = {
            "contributed_at": contributed_at.isoformat(),
            "decision_at": decision_at.isoformat(),
            "executed_at": "" if executed_at is None else executed_at.isoformat(),
            "execution_candle_open": ""
            if purchase is None
            else _canonical_decimal(purchase["execution_price"], "execution price"),
            "requested_gross_amount": _canonical_decimal(requested, "requested gross"),
            "decision_tag": f"{definition.implementation}.scheduled-buy",
            "oldest_pending_cash_age_seconds": oldest_age,
            "purchased_quantity": "0"
            if purchase is None
            else _canonical_decimal(purchase["quantity"], "purchase quantity"),
            "fee_paid": "0"
            if purchase is None
            else _canonical_decimal(purchase["fee_paid"], "purchase fee"),
            "deferral_seconds": 0
            if executed_at is None
            else int((executed_at - decision_at).total_seconds()),
        }
        records.append(
            _record(
                definition,
                "strategy_decision",
                decision_at,
                executed_gross_amount=_canonical_decimal(executed, "executed gross"),
                skip_or_deferral_reason="no_eligible_execution_candle"
                if purchase is None
                else "",
                **common,
            )
        )
        if executed_at is not None and executed_at > decision_at:
            records.append(
                _record(
                    definition,
                    "execution_deferral",
                    decision_at,
                    skip_or_deferral_reason="first_eligible_candle_after_decision",
                    **common,
                )
            )
        if purchase is not None:
            records.append(
                _record(
                    definition,
                    "purchase_execution",
                    executed_at,
                    executed_gross_amount=_canonical_decimal(executed, "executed gross"),
                    **common,
                )
            )
    if by_key:
        raise ValueError("purchase ledger contains executions without a strategy decision")

    records.sort(key=_ledger_sort_key)
    running_cash = Decimal("0")
    for record in records:
        record["available_cash_before"] = _canonical_decimal(
            running_cash, "cash before ledger record"
        )
        if record["record_type"] == "contribution_event":
            running_cash += _decimal(record["event_amount"], "event amount")
        elif record["record_type"] == "purchase_execution":
            running_cash -= _decimal(record["executed_gross_amount"], "executed gross")
        if running_cash < 0:
            raise ValueError("decision ledger produces negative cash")
        record["cash_balance_after_record"] = _canonical_decimal(
            running_cash, "cash after ledger record"
        )
    for index, record in enumerate(records):
        record["record_id"] = f"{definition.strategy_id}:{index:06d}:{record['record_type']}"
        records[index] = {field: record[field] for field in DECISION_LEDGER_FIELDS}
    validate_decision_ledger(records)
    return records


def validate_decision_ledger(records: object) -> list[dict[str, Any]]:
    """Reject duplicate, unordered, non-finite or impossible audit transitions."""
    if not isinstance(records, list) or not records:
        raise ValueError("decision ledger must be a non-empty list")
    identifiers: set[str] = set()
    decision_keys: set[tuple[str, str, str]] = set()
    previous_timestamp: datetime | None = None
    running_cash = Decimal("0")
    for position, item in enumerate(records):
        if not isinstance(item, dict):
            raise ValueError("decision ledger rows must be objects")
        missing = [field for field in DECISION_LEDGER_FIELDS if field not in item]
        extra = [field for field in item if field not in DECISION_LEDGER_FIELDS]
        if missing or extra:
            raise ValueError(
                f"decision ledger row {position} has invalid fields; "
                f"missing={missing}, extra={extra}"
            )
        record_id = item["record_id"]
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("decision ledger record_id must be non-empty")
        if record_id in identifiers:
            raise ValueError("decision ledger contains duplicate record ids")
        identifiers.add(record_id)
        record_type = item["record_type"]
        if record_type not in _RECORD_TYPES:
            raise ValueError(f"unsupported decision ledger record type: {record_type}")
        timestamp = _timestamp(item["timestamp"], "decision ledger timestamp")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("decision ledger timestamps must be chronological")
        previous_timestamp = timestamp
        if record_type == "strategy_decision":
            key = (item["strategy_id"], item["decision_at"], item["contributed_at"])
            if key in decision_keys:
                raise ValueError("decision ledger contains duplicate decisions")
            decision_keys.add(key)

        before = _decimal(item["available_cash_before"], "available cash before")
        after = _decimal(item["cash_balance_after_record"], "cash after record")
        event_amount = _decimal(item["event_amount"], "event amount")
        executed = _decimal(item["executed_gross_amount"], "executed gross amount")
        for field in ("requested_gross_amount", "purchased_quantity", "fee_paid"):
            _decimal(item[field], field.replace("_", " "))
        if before != running_cash:
            raise ValueError("decision ledger cash-before transition is impossible")
        expected = running_cash
        if record_type == "contribution_event":
            expected += event_amount
        elif record_type == "purchase_execution":
            expected -= executed
        if expected < 0 or after != expected:
            raise ValueError("decision ledger cash-after transition is impossible")
        running_cash = after
        for field in ("oldest_pending_cash_age_seconds", "deferral_seconds"):
            if not isinstance(item[field], int) or item[field] < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        try:
            indicator_values = json.loads(item["indicator_values"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("indicator values must be valid JSON") from exc
        if not isinstance(indicator_values, dict):
            raise ValueError("indicator values must decode to an object")
        for field in ("state_digest_before", "state_digest_after"):
            digest = item[field]
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                raise ValueError(f"{field} must be a SHA-256 digest")
    return records


def _time_weighted_curve_metrics(
    equity_curve: Sequence[Mapping[str, Any]], period_end: datetime
) -> tuple[Decimal, Decimal, Decimal]:
    if not equity_curve:
        raise ValueError("DCA metrics require a non-empty equity curve")
    rows = sorted(
        equity_curve,
        key=lambda row: _timestamp(row["timestamp"], "equity timestamp"),
    )
    if list(equity_curve) != rows:
        raise ValueError("equity curve timestamps must be ordered")
    total_seconds = weighted_cash = weighted_ratio = maximum_cash = Decimal("0")
    for index, row in enumerate(rows):
        start = _timestamp(row["timestamp"], "equity timestamp")
        stop = (
            _timestamp(rows[index + 1]["timestamp"], "equity timestamp")
            if index + 1 < len(rows)
            else period_end
        )
        if stop < start:
            raise ValueError("equity curve timestamps must be chronological")
        seconds = Decimal(str((stop - start).total_seconds()))
        cash = _decimal(row["cash_balance"], "equity cash")
        contributions = _decimal(row["cumulative_contributions"], "equity contributions")
        invested = _decimal(row["capital_invested"], "equity invested capital")
        if invested > contributions:
            raise ValueError("equity curve invests more than cumulative contributions")
        ratio = Decimal("0") if contributions == 0 else invested / contributions
        weighted_cash += cash * seconds
        weighted_ratio += ratio * seconds
        total_seconds += seconds
        maximum_cash = max(maximum_cash, cash)
    if total_seconds <= 0:
        raise ValueError("equity curve must span a positive duration")
    return weighted_cash / total_seconds, maximum_cash, weighted_ratio / total_seconds


def build_dca_metrics(
    *,
    result: Mapping[str, Any],
    events: Sequence[CashFlowEvent],
    decision_ledger: Sequence[Mapping[str, Any]],
    period_end: datetime,
) -> dict[str, Any]:
    """Build exact deployment, cash-age and decision-count metrics."""
    period_end = _timestamp(period_end, "period end")
    validate_decision_ledger(list(decision_ledger))
    total_contributions = sum((event.amount for event in events), Decimal("0"))
    quantity = _decimal(result["quantity_exact"], "final crypto quantity")
    final_cash = _decimal(result["cash_balance_exact"], "final cash")
    average_cash, maximum_cash, deployment_ratio = _time_weighted_curve_metrics(
        result["equity_curve"], period_end
    )
    execution_rows = [
        row for row in decision_ledger if row["record_type"] == "purchase_execution"
    ]
    decision_rows = [
        row for row in decision_ledger if row["record_type"] == "strategy_decision"
    ]
    gross_total = sum(
        (_decimal(row["executed_gross_amount"], "executed gross") for row in execution_rows),
        Decimal("0"),
    )
    delays = [
        int(
            (
                _timestamp(row["executed_at"], "execution timestamp")
                - _timestamp(row["contributed_at"], "contribution timestamp")
            ).total_seconds()
        )
        for row in execution_rows
    ]
    delay_weight = sum(
        (
            _decimal(row["executed_gross_amount"], "executed gross") * Decimal(delay)
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
        contributed_at = _timestamp(row["contributed_at"], "ledger contributed_at")
        if contributed_at not in remaining:
            raise ValueError("decision ledger execution references an unknown contribution")
        remaining[contributed_at] -= _decimal(row["executed_gross_amount"], "executed gross")
        if remaining[contributed_at] < 0:
            raise ValueError("decision ledger overspends a contribution bucket")
    oldest_age = max(
        (
            int((period_end - contributed_at).total_seconds())
            for contributed_at, amount in remaining.items()
            if amount > Decimal("1e-24")
        ),
        default=0,
    )
    if total_contributions <= 0:
        raise ValueError("DCA metrics require positive total contributions")
    deployed_within = {
        days: sum(
            (
                _decimal(row["executed_gross_amount"], "executed gross")
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
        "final_crypto_quantity": _canonical_decimal(quantity, "final quantity"),
        "crypto_quantity_per_unit_contributed": _canonical_decimal(
            quantity / total_contributions, "quantity per contribution"
        ),
        "average_uninvested_cash": _canonical_decimal(average_cash, "average cash"),
        "maximum_uninvested_cash": _canonical_decimal(maximum_cash, "maximum cash"),
        "time_weighted_capital_deployment_ratio": _canonical_decimal(
            deployment_ratio, "deployment ratio"
        ),
        "average_contribution_to_purchase_delay_seconds": _canonical_decimal(
            average_delay, "average purchase delay"
        ),
        "maximum_contribution_to_purchase_delay_seconds": max(delays, default=0),
        "oldest_uninvested_cash_age_seconds": oldest_age,
        **{
            f"contributions_deployed_within_{days}_days": _canonical_decimal(
                deployed_within[days] / total_contributions,
                f"{days}-day deployment",
            )
            for days in (7, 30, 90, 180)
        },
        "decision_count": len(decision_rows),
        "buy_count": len(execution_rows),
        "no_buy_count": sum(
            _decimal(row["executed_gross_amount"], "executed gross") == 0
            for row in decision_rows
        ),
        "final_uninvested_cash": _canonical_decimal(final_cash, "final cash"),
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
        if not Decimal("0") <= _decimal(metrics[field], field) <= Decimal("1"):
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


def apply_monthly_dca_reference(benchmarks: Sequence[dict[str, Any]]) -> None:
    """Record each passive result's exact final-value difference from Monthly DCA."""
    monthly = [row for row in benchmarks if row.get("benchmark") == "MonthlyDCA"]
    if len(monthly) > 1:
        raise ValueError("passive result contains duplicate MonthlyDCA rows")
    reference = (
        None
        if not monthly
        else _decimal(monthly[0]["final_value_exact"], "MonthlyDCA final value")
    )
    for row in benchmarks:
        metrics = row.get("dca_metrics")
        if not isinstance(metrics, dict):
            raise ValueError("passive strategy result is missing DCA metrics")
        if reference is None:
            metrics["final_value_difference_vs_monthly_dca"] = None
        else:
            difference = _decimal(row["final_value_exact"], "final value") - reference
            metrics["final_value_difference_vs_monthly_dca"] = _canonical_decimal(
                difference, "final value difference", allow_negative=True
            )


def _stem(benchmark: Mapping[str, Any]) -> str:
    return (
        str(benchmark["benchmark"])
        .replace("And", "-and-")
        .replace("DCA", "-dca")
        .lower()
        + "-"
        + str(benchmark["pair"]).replace("/", "-").lower()
    )


def write_dca_audit_csvs(result: Mapping[str, Any], output_dir: Path) -> None:
    """Write exact flat decision ledgers and a stable DCA comparison table."""
    benchmarks = result.get("benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        raise ValueError("DCA CSV output requires benchmark rows")
    output_dir.mkdir(parents=True, exist_ok=True)
    metric_rows = []
    for benchmark in benchmarks:
        ledger = validate_decision_ledger(benchmark.get("decision_ledger"))
        with (output_dir / f"{_stem(benchmark)}-decision-ledger.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(DECISION_LEDGER_FIELDS))
            writer.writeheader()
            writer.writerows(ledger)
        metrics = benchmark.get("dca_metrics")
        if not isinstance(metrics, dict):
            raise ValueError("benchmark is missing DCA metrics")
        metric_rows.append(
            {
                "category": "passive",
                "method": benchmark["benchmark"],
                "pair": benchmark["pair"],
                **metrics,
            }
        )
    with (output_dir / "dca-performance-metrics.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metric_rows[0]))
        writer.writeheader()
        writer.writerows(metric_rows)
    comparison_fields = [
        "category",
        "method",
        "pair",
        "final_crypto_quantity",
        "final_uninvested_cash",
        "time_weighted_capital_deployment_ratio",
        "oldest_uninvested_cash_age_seconds",
        "decision_count",
        "buy_count",
        "no_buy_count",
        "deployment_classification",
        "final_value_difference_vs_monthly_dca",
    ]
    with (output_dir / "dca-comparison.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=comparison_fields)
        writer.writeheader()
        writer.writerows(
            {field: row[field] for field in comparison_fields} for row in metric_rows
        )
