"""Validate active experiment identity and effective settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from roundup_crypto_lab.active_common import (
    CAPITAL_MODES,
    _mapping,
    _nonnegative,
    _positive,
    identity,
    ts,
)
from roundup_crypto_lab.passive_benchmarks import parse_timerange


def _validate_experiment(
    experiment: dict[str, Any], expected: dict[str, object]
) -> tuple[datetime, datetime]:
    for key, wanted in expected.items():
        actual_key = "selected_pair" if key == "pair" else key
        if wanted is not None and experiment.get(actual_key) != wanted:
            raise ValueError(f"unexpected {key}")
    start = ts(experiment.get("start"), "start")
    end = ts(experiment.get("end"), "end")
    if start >= end or parse_timerange(str(experiment.get("timerange"))) != (start, end):
        raise ValueError("inconsistent timerange")
    if experiment.get("capital_mode") not in CAPITAL_MODES:
        raise ValueError("unsupported capital mode")
    if experiment.get("experiment_id") != identity(experiment):
        raise ValueError("inconsistent experiment identity")
    if not isinstance(experiment.get("execution_model"), str) or not experiment["execution_model"]:
        raise ValueError("missing execution model")
    scope = _mapping(experiment.get("execution_scope"), "execution scope")
    for key in ("selected_pair", "timeframe", "config_digest", "generated_config"):
        if not isinstance(scope.get(key), str) or not scope[key]:
            raise ValueError(f"execution scope lacks {key}")
    if scope["selected_pair"] != experiment.get("selected_pair"):
        raise ValueError("execution scope pair differs")
    if scope["timeframe"] != experiment.get("timeframe"):
        raise ValueError("execution scope timeframe differs")
    plan = _mapping(experiment.get("investment_plan"), "investment plan")
    _positive(plan.get("initial_capital"), "initial capital")
    _positive(plan.get("monthly_budget"), "monthly budget")
    fee = _nonnegative(plan.get("fee_ratio"), "plan fee ratio")
    if fee >= 1:
        raise ValueError("plan fee ratio must be lower than one")
    day = plan.get("contribution_day")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        raise ValueError("contribution day must be an integer from 1 through 31")
    settings = _mapping(experiment.get("effective_settings"), "effective settings")
    settings_fee = _nonnegative(settings.get("fee_ratio"), "effective fee ratio")
    if settings_fee != fee:
        raise ValueError("investment-plan and effective fees differ")
    return start, end
