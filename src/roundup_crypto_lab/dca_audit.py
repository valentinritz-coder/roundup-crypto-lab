"""Public DCA audit API with residual-safe ledger validation."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from roundup_crypto_lab import dca_audit_residual as _base

DCA_RESULT_SCHEMA_VERSION = _base.DCA_RESULT_SCHEMA_VERSION
DECISION_LEDGER_SCHEMA_VERSION = _base.DECISION_LEDGER_SCHEMA_VERSION
DCA_METRICS_SCHEMA_VERSION = _base.DCA_METRICS_SCHEMA_VERSION
DECISION_LEDGER_FIELDS = _base.DECISION_LEDGER_FIELDS

_ACCOUNTING_EPSILON = Decimal("1e-24")


def _balance(value: Decimal, message: str) -> Decimal:
    if not value.is_finite() or value < -_ACCOUNTING_EPSILON:
        raise ValueError(message)
    return Decimal("0") if abs(value) <= _ACCOUNTING_EPSILON else value


def validate_decision_ledger(records: object) -> list[dict[str, Any]]:
    """Reject malformed ledgers while canonicalizing only sub-quantum residue."""
    if not isinstance(records, list) or not records:
        raise ValueError("decision ledger must be a non-empty list")
    identifiers: set[str] = set()
    decision_keys: set[tuple[str, str, str]] = set()
    previous_timestamp = None
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
        if record_type not in _base._impl._RECORD_TYPES:
            raise ValueError(f"unsupported decision ledger record type: {record_type}")
        timestamp = _base._impl._timestamp(item["timestamp"], "decision ledger timestamp")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("decision ledger timestamps must be chronological")
        previous_timestamp = timestamp
        if record_type == "strategy_decision":
            key = (item["strategy_id"], item["decision_at"], item["contributed_at"])
            if key in decision_keys:
                raise ValueError("decision ledger contains duplicate decisions")
            decision_keys.add(key)
        before = _base._impl._decimal(
            item["available_cash_before"], "available cash before"
        )
        after = _base._impl._decimal(item["cash_balance_after_record"], "cash after record")
        event_amount = _base._impl._decimal(item["event_amount"], "event amount")
        executed = _base._impl._decimal(
            item["executed_gross_amount"], "executed gross amount"
        )
        for field in ("requested_gross_amount", "purchased_quantity", "fee_paid"):
            _base._impl._decimal(item[field], field.replace("_", " "))
        if before != running_cash:
            raise ValueError("decision ledger cash-before transition is impossible")
        expected = running_cash
        if record_type == "contribution_event":
            expected += event_amount
        elif record_type == "purchase_execution":
            expected -= executed
        expected = _balance(expected, "decision ledger cash-after transition is impossible")
        if after != expected:
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


# The residual-safe implementation resolves this global at call time, so patch
# its validator once and keep the public module compact.
_base.validate_decision_ledger = validate_decision_ledger

state_digest = _base.state_digest
build_baseline_decision_ledger = _base.build_baseline_decision_ledger
build_dca_metrics = _base.build_dca_metrics
enrich_dca_strategy_result = _base.enrich_dca_strategy_result
apply_monthly_dca_reference = _base.apply_monthly_dca_reference
write_dca_audit_csvs = _base.write_dca_audit_csvs
