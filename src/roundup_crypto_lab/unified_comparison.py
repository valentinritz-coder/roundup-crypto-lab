"""Unify active and passive results into strictly comparable scenario groups."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "unified-scenario-comparison/v1"
METRIC_SCHEMA_VERSION = "cash-flow-metrics/v1"


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _rows(value: object, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be boolean")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be decimal") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_metrics(value: object) -> dict[str, Any]:
    metrics = _mapping(value, "cash-flow metrics")
    if metrics.get("schema_version") != METRIC_SCHEMA_VERSION:
        raise ValueError("unsupported cash-flow metric schema")
    decimal_fields = (
        "initial_capital",
        "monthly_budget",
        "total_contributions",
        "total_fees",
        "final_value",
        "final_cash",
        "final_asset_value",
        "terminal_liquidation_value",
        "profit_abs",
        "simple_return_on_contributions",
        "time_weighted_return",
        "max_drawdown_time_weighted",
        "max_drawdown_raw_portfolio",
        "average_capital_deployed",
        "capital_utilization_ratio",
    )
    for field in decimal_fields:
        _decimal(metrics.get(field), field)
    xirr = metrics.get("money_weighted_return")
    if xirr is not None:
        _decimal(xirr, "money-weighted return")
    _text(metrics.get("money_weighted_return_status"), "XIRR status")
    for field in (
        "max_drawdown_time_weighted",
        "max_drawdown_raw_portfolio",
    ):
        value_decimal = _decimal(metrics[field], field)
        if not 0 <= value_decimal <= 1:
            raise ValueError(f"{field} must be between zero and one")
    utilization = _decimal(
        metrics["capital_utilization_ratio"],
        "capital utilization",
    )
    if not 0 <= utilization <= 1:
        raise ValueError("capital utilization must be between zero and one")
    return metrics


def _schedule(value: object) -> tuple[list[dict[str, Any]], str]:
    rows = _rows(value, "contribution schedule")
    normalized = []
    for item in rows:
        row = _mapping(item, "contribution schedule row")
        normalized.append(
            {
                "contributed_at": _text(
                    row.get("contributed_at"),
                    "contribution timestamp",
                ),
                "amount": str(
                    _decimal(row.get("amount"), "contribution amount")
                ),
                "kind": _text(row.get("kind"), "contribution kind"),
            }
        )
    return normalized, _canonical_hash(normalized)


def _scenario(
    *,
    pair: object,
    timeframe: object,
    timerange: object,
    plan: dict[str, Any],
    schedule: object,
    capital_mode: object,
    repository_commit: object,
) -> dict[str, Any]:
    normalized_schedule, schedule_hash = _schedule(schedule)
    scenario = {
        "pair": _text(pair, "pair"),
        "timeframe": _text(timeframe, "timeframe"),
        "timerange": _text(timerange, "timerange"),
        "initial_capital": str(
            _decimal(plan.get("initial_capital"), "initial capital")
        ),
        "monthly_budget": str(
            _decimal(plan.get("monthly_budget"), "monthly budget")
        ),
        "contribution_day": plan.get("contribution_day"),
        "contribution_schedule": normalized_schedule,
        "contribution_schedule_hash": schedule_hash,
        "fee_ratio": str(_decimal(plan.get("fee_ratio"), "fee ratio")),
        "capital_mode": _text(capital_mode, "capital mode"),
        "repository_commit": _text(
            repository_commit,
            "repository commit",
        ),
    }
    day = scenario["contribution_day"]
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        raise ValueError("contribution day must be an integer from 1 through 31")
    scenario["scenario_id"] = _canonical_hash(scenario)
    return scenario


def _active_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = _mapping(
        payload.get("native_metadata"),
        "active native metadata",
    )
    commit = metadata.get("commit_sha")
    rows = []
    active_results = _rows(
        payload.get("active_investor_cash_flow_simulation"),
        "active results",
    )
    for item in active_results:
        result = _mapping(item, "active result")
        experiment = _mapping(result.get("experiment"), "active experiment")
        plan = _mapping(
            experiment.get("investment_plan"),
            "active investment plan",
        )
        scenario = _scenario(
            pair=experiment.get("selected_pair"),
            timeframe=experiment.get("timeframe"),
            timerange=experiment.get("timerange"),
            plan=plan,
            schedule=result.get("contribution_schedule"),
            capital_mode=experiment.get("capital_mode"),
            repository_commit=commit,
        )
        metrics = _validate_metrics(result.get("cash_flow_metrics"))
        legacy = _mapping(result.get("adapter_metrics"), "adapter metrics")
        rows.append(
            {
                "scenario": scenario,
                "category": "active",
                "method": _text(experiment.get("strategy"), "strategy"),
                "method_detail": _text(
                    experiment.get("execution_model"),
                    "execution model",
                ),
                "result_schema_version": _text(
                    result.get("schema_version"),
                    "result schema",
                ),
                "number_of_actions": legacy.get("entry_count"),
                "metrics": metrics,
            }
        )
    return rows


def _passive_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = _mapping(payload.get("metadata"), "passive metadata")
    plan = {
        "initial_capital": metadata.get("initial_capital"),
        "monthly_budget": metadata.get("monthly_budget"),
        "contribution_day": metadata.get("contribution_day"),
        "fee_ratio": metadata.get("fee"),
    }
    rows = []
    for item in _rows(payload.get("benchmarks"), "passive results"):
        result = _mapping(item, "passive result")
        scenario = _scenario(
            pair=result.get("pair"),
            timeframe=metadata.get("timeframe"),
            timerange=metadata.get("timerange"),
            plan=plan,
            schedule=result.get("contribution_schedule"),
            capital_mode=metadata.get("capital_mode"),
            repository_commit=metadata.get("repository_commit"),
        )
        rows.append(
            {
                "scenario": scenario,
                "category": "passive",
                "method": _text(result.get("benchmark"), "benchmark"),
                "method_detail": _text(
                    result.get("deployment_method"),
                    "deployment method",
                ),
                "result_schema_version": _text(
                    payload.get("schema_version"),
                    "passive schema",
                ),
                "number_of_actions": result.get("number_of_buys"),
                "metrics": _validate_metrics(
                    result.get("cash_flow_metrics")
                ),
            }
        )
    return rows


def _rank_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    identifiers = set()
    for row in rows:
        identifier = (row["category"], row["method"])
        if identifier in identifiers:
            raise ValueError("duplicate scenario and method identifier")
        identifiers.add(identifier)
        actions = row["number_of_actions"]
        if (
            isinstance(actions, bool)
            or not isinstance(actions, int)
            or actions < 0
        ):
            raise ValueError("number of actions must be a non-negative integer")
    skill = sorted(
        rows,
        key=lambda row: (
            -_decimal(row["metrics"]["time_weighted_return"], "TWR"),
            _decimal(
                row["metrics"]["max_drawdown_time_weighted"],
                "TWR drawdown",
            ),
            row["category"],
            row["method"],
        ),
    )
    investor = sorted(
        rows,
        key=lambda row: (
            -_decimal(row["metrics"]["final_value"], "final value"),
            -_decimal(row["metrics"]["profit_abs"], "profit"),
            row["category"],
            row["method"],
        ),
    )
    skill_rank = {
        (row["category"], row["method"]): index + 1
        for index, row in enumerate(skill)
    }
    investor_rank = {
        (row["category"], row["method"]): index + 1
        for index, row in enumerate(investor)
    }
    ranked = []
    ordered = sorted(
        rows,
        key=lambda item: (item["category"], item["method"]),
    )
    for row in ordered:
        key = (row["category"], row["method"])
        ranked.append(
            {
                **row,
                "strategy_skill_rank": skill_rank[key],
                "investor_outcome_rank": investor_rank[key],
            }
        )
    return ranked


def build_unified_comparison(
    active_payloads: list[dict[str, Any]],
    passive_payloads: list[dict[str, Any]],
    *,
    strict_single_scenario: bool = False,
) -> dict[str, Any]:
    rows = []
    for payload in active_payloads:
        rows.extend(_active_rows(payload))
    for payload in passive_payloads:
        rows.extend(_passive_rows(payload))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["scenario"]["scenario_id"], []).append(row)
    if strict_single_scenario and len(grouped) != 1:
        raise ValueError(
            "active and passive inputs do not share one comparable scenario"
        )
    groups = []
    for scenario_id in sorted(grouped):
        group_rows = grouped[scenario_id]
        categories = {row["category"] for row in group_rows}
        if strict_single_scenario and categories != {"active", "passive"}:
            raise ValueError("strict comparison requires active and passive rows")
        groups.append(
            {
                "scenario": group_rows[0]["scenario"],
                "categories": sorted(categories),
                "rows": _rank_group(group_rows),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "interpretations": {
            "strategy_skill": (
                "Ranked primarily by TWR, then contribution-neutral drawdown."
            ),
            "investor_outcome": (
                "Ranked primarily by final value, then absolute profit."
            ),
            "pair_scope": (
                "Pair groups are alternative scenarios, not a diversified portfolio."
            ),
        },
        "scenario_groups": groups,
    }


def write_csv(payload: dict[str, Any], path: Path) -> None:
    rows = []
    for group in payload["scenario_groups"]:
        scenario = group["scenario"]
        for row in group["rows"]:
            rows.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "pair": scenario["pair"],
                    "timeframe": scenario["timeframe"],
                    "timerange": scenario["timerange"],
                    "capital_mode": scenario["capital_mode"],
                    "category": row["category"],
                    "method": row["method"],
                    "number_of_actions": row["number_of_actions"],
                    "strategy_skill_rank": row["strategy_skill_rank"],
                    "investor_outcome_rank": row["investor_outcome_rank"],
                    **row["metrics"],
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _format_xirr(metrics: dict[str, Any]) -> str:
    value = metrics["money_weighted_return"]
    if value is not None:
        return f"{Decimal(value):.2%}"
    return f"N/A ({metrics['money_weighted_return_status']})"


def _markdown_row(row: dict[str, Any]) -> str:
    metrics = row["metrics"]
    values = (
        row["strategy_skill_rank"],
        row["investor_outcome_rank"],
        row["category"],
        row["method"],
        row["number_of_actions"],
        f"{Decimal(metrics['final_value']):.2f}",
        f"{Decimal(metrics['profit_abs']):.2f}",
        f"{Decimal(metrics['time_weighted_return']):.2%}",
        _format_xirr(metrics),
        f"{Decimal(metrics['total_fees']):.2f}",
        f"{Decimal(metrics['final_cash']):.2f}",
        f"{Decimal(metrics['capital_utilization_ratio']):.2%}",
        f"{Decimal(metrics['max_drawdown_time_weighted']):.2%}",
        f"{Decimal(metrics['max_drawdown_raw_portfolio']):.2%}",
    )
    return "| " + " | ".join(str(value) for value in values) + " |"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Unified scenario comparison", ""]
    header = (
        "| Skill rank | Outcome rank | Category | Method | Actions | "
        "Final value | Profit | TWR | XIRR | Fees | Cash | Utilization | "
        "TWR DD | Raw DD |"
    )
    separator = (
        "| ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for group in payload["scenario_groups"]:
        scenario = group["scenario"]
        scenario_line = (
            f"Scenario `{scenario['scenario_id']}` · "
            f"`{scenario['timerange']}` · `{scenario['timeframe']}`"
        )
        lines += [
            f"## {scenario['pair']} · {scenario['capital_mode']}",
            "",
            scenario_line,
            "",
            header,
            separator,
        ]
        ordered = sorted(
            group["rows"],
            key=lambda item: item["strategy_skill_rank"],
        )
        lines.extend(_markdown_row(row) for row in ordered)
        lines += [
            "",
            "Strategy skill and investor outcome are separate rankings. "
            "This pair-specific group is not a diversified portfolio.",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active", action="append", type=Path, default=[])
    parser.add_argument("--passive", action="append", type=Path, default=[])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--strict-single-scenario", action="store_true")
    args = parser.parse_args()
    if not args.active or not args.passive:
        parser.error("at least one active and one passive artifact are required")
    active = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in args.active
    ]
    passive = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in args.passive
    ]
    payload = build_unified_comparison(
        active,
        passive,
        strict_single_scenario=args.strict_single_scenario,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_csv(payload, args.csv)
    args.summary.write_text(
        render_markdown(payload) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
