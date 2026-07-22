"""Validate seven-result, native-metadata, and differential consistency."""

from __future__ import annotations

from typing import Any

from roundup_crypto_lab.active_common import _mapping, _rows, dec
from roundup_crypto_lab.active_result_validation import validate_active_result
from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER

DIFFERENTIAL_SCHEMA_VERSION = "one-shot-differential/v1"


def validate_result_set(results: list[dict[str, object]]) -> dict[str, Any]:
    if len(results) != len(STRATEGY_ORDER):
        raise ValueError("exactly seven active results required")
    for result in results:
        validate_active_result(result)
    experiments = [_mapping(result.get("experiment"), "experiment") for result in results]
    if [experiment.get("strategy") for experiment in experiments] != list(STRATEGY_ORDER):
        raise ValueError("active strategies must be complete and ordered")
    if len({experiment.get("experiment_id") for experiment in experiments}) != 1:
        raise ValueError("active results do not share one experiment")
    return experiments[0]


def validate_native_metadata(metadata: dict[str, Any], experiment: dict[str, Any]) -> None:
    scope = _mapping(experiment.get("execution_scope"), "execution scope")
    plan = _mapping(experiment.get("investment_plan"), "investment plan")
    settings = _mapping(experiment.get("effective_settings"), "effective settings")
    checks = {
        "timerange": experiment.get("timerange"),
        "timeframe": experiment.get("timeframe"),
        "pairs": experiment.get("selected_pair"),
        "config": scope.get("generated_config"),
        "config_digest": scope.get("config_digest"),
        "strategies": list(STRATEGY_ORDER),
    }
    if any(metadata.get(key) != value for key, value in checks.items()):
        raise ValueError("native metadata differs from active experiment")
    if dec(metadata.get("starting_balance"), "native starting balance") != dec(
        plan.get("initial_capital"), "initial capital"
    ):
        raise ValueError("native starting balance differs")
    if dec(metadata.get("fee"), "native fee") != dec(settings.get("fee_ratio"), "active fee"):
        raise ValueError("native fee differs")


def validate_differential(
    differential: dict[str, Any], experiment: dict[str, Any]
) -> list[dict[str, Any]]:
    if differential.get("schema_version") != DIFFERENTIAL_SCHEMA_VERSION:
        raise ValueError("invalid one-shot differential schema")
    for key in ("experiment_id", "selected_pair", "timeframe", "timerange", "capital_mode"):
        if differential.get(key) != experiment.get(key):
            raise ValueError(f"differential {key} differs")
    rows = _rows(differential.get("strategies"), "differential strategies")
    if [row.get("strategy") for row in rows if isinstance(row, dict)] != list(STRATEGY_ORDER):
        raise ValueError("differential strategies must be complete and ordered")
    for row_value in rows:
        row = _mapping(row_value, "differential strategy")
        if row.get("status") != "passed":
            raise ValueError("differential strategy did not pass")
        count = row.get("trade_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("differential trade count is invalid")
        if row.get("checked_fields") != ["lifecycle", "final_balances"]:
            raise ValueError("differential checked fields are invalid")
    return [dict(row) for row in rows if isinstance(row, dict)]
