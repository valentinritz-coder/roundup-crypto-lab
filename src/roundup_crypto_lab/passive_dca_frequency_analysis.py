"""Robust phase-aggregated analysis for passive DCA frequency campaigns."""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.dca_costed_execution import registered_frequency_strategies
from roundup_crypto_lab.dca_registry import StrategyRegistry
from roundup_crypto_lab.deployment_engine import parse_timerange
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule
from roundup_crypto_lab.passive_dca_frequency_campaign import (
    EXPECTED_PHASES,
    FREQUENCY_ORDER,
    frequency_metadata,
    load_json,
    plan_campaign,
)

ANALYSIS_SCHEMA_VERSION = "passive-dca-frequency-analysis/v1"
CLASSIFICATION_SCHEMA_VERSION = "passive-dca-frequency-classification/v1"
FRICTIONLESS_PROFILE_ID = "frictionless-control-v1"


def _decimal(value: object, name: str = "value") -> Decimal:
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _canonical(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _median(values: Iterable[Decimal]) -> Decimal | None:
    materialized = list(values)
    return statistics.median(materialized) if materialized else None


def _mean(values: Iterable[Decimal]) -> Decimal | None:
    materialized = list(values)
    if not materialized:
        return None
    return sum(materialized, Decimal("0")) / Decimal(len(materialized))


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _phase_sort(value: int | None) -> int:
    return -1 if value is None else value


def _phase_label(value: int | None) -> str:
    return "predefined-weekday" if value is None else str(value)


def _scenario_contributions(
    scenario: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[dict[datetime, Decimal], datetime]:
    start, end = parse_timerange(str(scenario["timerange"]))
    plan = InvestmentPlan(
        str(scenario["initial_capital"]),
        str(scenario["monthly_budget"]),
        str(profile["trading_fee_ratio"]),
        int(scenario["contribution_day"]),
    )
    balances: dict[datetime, Decimal] = defaultdict(lambda: Decimal("0"))
    for event in contribution_schedule(plan, start, end):
        balances[event.contributed_at.astimezone(UTC)] += event.amount
    return dict(balances), end


def _deployment_metrics(
    result: Mapping[str, Any],
    scenario: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Decimal]:
    balances, period_end = _scenario_contributions(scenario, profile)
    total = sum(balances.values(), Decimal("0"))
    weighted_age = Decimal("0")
    maximum_age = Decimal("0")

    ledger = result.get("purchase_ledger", [])
    if not isinstance(ledger, list):
        raise ValueError("purchase_ledger must be an array")
    for purchase in ledger:
        if not isinstance(purchase, Mapping):
            raise ValueError("purchase_ledger rows must be objects")
        executed_at = _timestamp(purchase.get("executed_at"), "executed_at")
        allocations = purchase.get("funding_allocations", [])
        if not isinstance(allocations, list):
            raise ValueError("funding_allocations must be an array")
        for allocation in allocations:
            if not isinstance(allocation, Mapping):
                raise ValueError("funding allocations must be objects")
            contributed_at = _timestamp(
                allocation.get("contributed_at"), "funding contributed_at"
            )
            amount = _decimal(allocation.get("amount"), "funding amount")
            if contributed_at not in balances or amount > balances[contributed_at]:
                raise ValueError("funding allocation exceeds its contribution bucket")
            age = Decimal(str((executed_at - contributed_at).total_seconds()))
            if age < 0:
                raise ValueError("purchase consumes a future contribution")
            balances[contributed_at] -= amount
            weighted_age += amount * age
            maximum_age = max(maximum_age, age)

    for contributed_at, amount in balances.items():
        if amount < 0:
            raise ValueError("contribution bucket produced a negative residual")
        if amount == 0:
            continue
        age = Decimal(str((period_end - contributed_at).total_seconds()))
        if age < 0:
            raise ValueError("residual contribution lies after the scenario end")
        weighted_age += amount * age
        maximum_age = max(maximum_age, age)

    equity = result.get("equity_curve", [])
    if not isinstance(equity, list):
        raise ValueError("equity_curve must be an array")
    cash_values = [
        _decimal(row["cash_balance"], "cash_balance")
        for row in equity
        if isinstance(row, Mapping) and row.get("cash_balance") is not None
    ]
    contribution_values = [
        _decimal(row["cumulative_contributions"], "cumulative_contributions")
        for row in equity
        if isinstance(row, Mapping) and row.get("cumulative_contributions") is not None
    ]
    average_cash = _mean(cash_values) or Decimal("0")
    average_contributions = _mean(contribution_values) or Decimal("0")
    deployment_ratio = (
        Decimal("0")
        if average_contributions == 0
        else Decimal("1") - average_cash / average_contributions
    )
    return {
        "average_pending_cash_age_seconds": (
            Decimal("0") if total == 0 else weighted_age / total
        ),
        "maximum_pending_cash_age_seconds": maximum_age,
        "average_cash_balance": average_cash,
        "average_capital_deployment_ratio": deployment_ratio,
    }


def _phase_rows(
    *,
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    phase: str,
    result_files: Sequence[Path],
) -> list[dict[str, Any]]:
    expected_rows = [row for row in plan_campaign(campaign) if row["phase"] == phase]
    expected = {row["scenario_id"]: row for row in expected_rows}
    definitions = {
        definition.strategy_id: frequency_metadata(definition)
        for definition in registered_frequency_strategies(registry)
    }
    rows: list[dict[str, Any]] = []
    observed: set[str] = set()

    for source in sorted(result_files):
        payload = load_json(source)
        campaign_meta = payload.get("campaign", {})
        scenario_id = str(campaign_meta.get("scenario_id", ""))
        if scenario_id not in expected:
            raise ValueError(f"unplanned frequency scenario artifact: {source}")
        if scenario_id in observed:
            raise ValueError(f"duplicate frequency scenario artifact: {scenario_id}")
        if campaign_meta.get("phase") != phase:
            raise ValueError(f"scenario phase mismatch: {source}")
        if payload.get("registry", {}).get("registry_digest") != registry.digest:
            raise ValueError(f"scenario registry digest mismatch: {source}")
        planned = expected[scenario_id]
        profile = payload.get("execution_cost_profile", {})
        profile_id = str(profile.get("cost_profile_id", ""))
        if profile_id != planned["cost_profile_id"]:
            raise ValueError(f"scenario cost profile mismatch: {source}")
        scenario = payload.get("scenario", {})
        for name in (
            "pair",
            "timerange",
            "initial_capital",
            "monthly_budget",
            "contribution_day",
        ):
            if str(scenario.get(name)) != str(planned.get(name)):
                raise ValueError(f"scenario {name} mismatch: {source}")

        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError(f"scenario results must be an array: {source}")
        result_ids: set[str] = set()
        for result in results:
            if not isinstance(result, Mapping):
                raise ValueError("frequency results must be objects")
            strategy = result.get("strategy", {})
            strategy_id = str(strategy.get("strategy_id", ""))
            if strategy_id not in definitions:
                raise ValueError(f"unexpected strategy in frequency scenario: {strategy_id}")
            if strategy_id in result_ids:
                raise ValueError(f"duplicate strategy result in scenario: {strategy_id}")
            result_ids.add(strategy_id)
            frequency = definitions[strategy_id]
            costs = result.get("execution_costs", {})
            if not isinstance(costs, Mapping):
                raise ValueError("frequency result must contain execution_costs")
            deployment = _deployment_metrics(result, scenario, profile)
            final_value = _decimal(result.get("final_value_exact"), "final_value_exact")
            total_contributions = _decimal(
                scenario.get("total_contributions"), "total_contributions"
            )
            rows.append(
                {
                    "row_type": "phase_result",
                    "scenario_id": scenario_id,
                    "research_phase": phase,
                    "pair": planned["pair"],
                    "window_set_id": planned["window_set_id"],
                    "timerange": planned["timerange"],
                    "overlapping": planned["overlapping"],
                    "cost_profile_id": profile_id,
                    "cost_profile_digest": profile.get("profile_digest"),
                    "frequency": frequency["frequency"],
                    "phase_offset_months": frequency["phase_offset_months"],
                    "strategy_id": strategy_id,
                    "total_contributions": total_contributions,
                    "net_terminal_value": final_value,
                    "net_profit": final_value - total_contributions,
                    "asset_quantity": _decimal(
                        result.get("quantity_exact"), "quantity_exact"
                    ),
                    "final_uninvested_cash": _decimal(
                        result.get("cash_balance_exact"), "cash_balance_exact"
                    ),
                    "explicit_fees_paid": _decimal(
                        costs.get("explicit_fees_paid"), "explicit_fees_paid"
                    ),
                    "estimated_spread_cost": _decimal(
                        costs.get("estimated_spread_cost"), "estimated_spread_cost"
                    ),
                    "total_execution_cost": _decimal(
                        costs.get("total_execution_cost"), "total_execution_cost"
                    ),
                    "order_count": _decimal(costs.get("order_count"), "order_count"),
                    "average_order_size": _decimal(
                        costs.get("average_order_size"), "average_order_size"
                    ),
                    "contribution_neutralized_drawdown": _decimal(
                        result.get("max_drawdown_time_weighted", "0"),
                        "max_drawdown_time_weighted",
                    ),
                    **deployment,
                    "source": str(source),
                }
            )
        if result_ids != set(definitions):
            raise ValueError(f"scenario does not contain the complete frequency matrix: {source}")
        observed.add(scenario_id)

    missing = sorted(set(expected) - observed)
    if missing:
        raise ValueError(f"frequency analysis is missing scenarios: {missing}")

    frictionless = {
        (
            row["pair"],
            row["research_phase"],
            row["window_set_id"],
            row["timerange"],
            row["strategy_id"],
        ): row
        for row in rows
        if row["cost_profile_id"] == FRICTIONLESS_PROFILE_ID
    }
    for row in rows:
        key = (
            row["pair"],
            row["research_phase"],
            row["window_set_id"],
            row["timerange"],
            row["strategy_id"],
        )
        control = frictionless.get(key)
        if control is None:
            raise ValueError(f"missing frictionless peer for {row['strategy_id']}")
        row["gross_terminal_value"] = control["net_terminal_value"]
        row["gross_profit"] = (
            row["gross_terminal_value"] - row["total_contributions"]
        )
        row["execution_cost_terminal_impact"] = (
            row["gross_terminal_value"] - row["net_terminal_value"]
        )
    return sorted(
        rows,
        key=lambda row: (
            row["cost_profile_id"],
            row["window_set_id"],
            row["timerange"],
            FREQUENCY_ORDER.index(row["frequency"]),
            _phase_sort(row["phase_offset_months"]),
        ),
    )


def _dispersion(values: Sequence[Decimal]) -> Decimal:
    if len(values) <= 1:
        return Decimal("0")
    center = _median(values) or Decimal("0")
    spread = max(values) - min(values)
    return spread if center == 0 else spread / abs(center)


def _window_rows(phase_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in phase_rows:
        grouped[
            (
                row["pair"],
                row["research_phase"],
                row["window_set_id"],
                row["timerange"],
                row["overlapping"],
                row["cost_profile_id"],
                row["frequency"],
            )
        ].append(row)

    windows: list[dict[str, Any]] = []
    decimal_fields = (
        "net_terminal_value",
        "gross_terminal_value",
        "net_profit",
        "gross_profit",
        "execution_cost_terminal_impact",
        "asset_quantity",
        "final_uninvested_cash",
        "explicit_fees_paid",
        "estimated_spread_cost",
        "total_execution_cost",
        "order_count",
        "average_order_size",
        "average_pending_cash_age_seconds",
        "maximum_pending_cash_age_seconds",
        "average_cash_balance",
        "average_capital_deployment_ratio",
        "contribution_neutralized_drawdown",
    )
    for key, items in sorted(grouped.items(), key=str):
        pair, research_phase, window_set_id, timerange, overlapping, profile_id, frequency = key
        expected_phases = set(EXPECTED_PHASES[str(frequency)])
        actual_phases = {item["phase_offset_months"] for item in items}
        if actual_phases != expected_phases:
            raise ValueError(
                f"incomplete nuisance phases for {frequency} in {timerange}: {actual_phases}"
            )
        worst = min(items, key=lambda item: item["net_terminal_value"])
        row: dict[str, Any] = {
            "row_type": "frequency_window_aggregate",
            "pair": pair,
            "research_phase": research_phase,
            "window_set_id": window_set_id,
            "timerange": timerange,
            "overlapping": overlapping,
            "cost_profile_id": profile_id,
            "frequency": frequency,
            "phase_replication_count": len(items),
            "phase_offsets": sorted(actual_phases, key=_phase_sort),
            "phase_dispersion": _dispersion(
                [item["net_terminal_value"] for item in items]
            ),
            "worst_phase_offset_months": worst["phase_offset_months"],
            "worst_phase_terminal_value": worst["net_terminal_value"],
            "source_scenario_ids": sorted({str(item["scenario_id"]) for item in items}),
        }
        for field in decimal_fields:
            row[field] = _median(item[field] for item in items) or Decimal("0")
        row["maximum_pending_cash_age_seconds"] = max(
            item["maximum_pending_cash_age_seconds"] for item in items
        )
        windows.append(row)

    monthly = {
        (
            row["pair"],
            row["research_phase"],
            row["window_set_id"],
            row["timerange"],
            row["cost_profile_id"],
        ): row
        for row in windows
        if row["frequency"] == "monthly"
    }
    for row in windows:
        key = (
            row["pair"],
            row["research_phase"],
            row["window_set_id"],
            row["timerange"],
            row["cost_profile_id"],
        )
        control = monthly.get(key)
        if control is None:
            raise ValueError(f"missing MonthlyDCA control for {key}")
        row["net_difference_vs_monthly"] = (
            row["net_terminal_value"] - control["net_terminal_value"]
        )
        row["gross_timing_difference_vs_monthly"] = (
            row["gross_terminal_value"] - control["gross_terminal_value"]
        )
        row["differential_cost_impact_vs_monthly"] = (
            row["execution_cost_terminal_impact"]
            - control["execution_cost_terminal_impact"]
        )
        row["cash_balance_difference_vs_monthly"] = (
            row["average_cash_balance"] - control["average_cash_balance"]
        )
    return sorted(
        windows,
        key=lambda row: (
            row["cost_profile_id"],
            row["window_set_id"],
            row["timerange"],
            FREQUENCY_ORDER.index(row["frequency"]),
        ),
    )


def _classification(
    summary: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> str:
    if summary["frequency"] == "monthly":
        return "primary control"
    thresholds = policy["thresholds"]
    minimum = int(
        thresholds[
            "minimum_exploratory_windows"
            if summary["research_phase"] == "exploratory"
            else "minimum_confirmation_windows"
        ]
    )
    if int(summary["windows_evaluated"]) < minimum:
        return "insufficient evidence"
    win_rate = _decimal(summary["win_rate_vs_monthly"])
    dispersion = _decimal(summary["maximum_phase_dispersion"])
    if (
        win_rate >= _decimal(thresholds["robust_final_value_win_rate"])
        and dispersion
        <= _decimal(thresholds["maximum_phase_dispersion_for_robust_result"])
    ):
        return "robust improvement"
    if win_rate >= _decimal(thresholds["promising_final_value_win_rate"]):
        return "promising but phase-sensitive" if dispersion > 0 else "promising"
    if win_rate <= _decimal(thresholds["rejected_final_value_win_rate_max"]):
        return "rejected"
    return "mixed"


def _frequency_summaries(
    window_rows: Sequence[Mapping[str, Any]],
    phase_rows: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in window_rows:
        grouped[
            (
                row["pair"],
                row["research_phase"],
                row["window_set_id"],
                row["overlapping"],
                row["cost_profile_id"],
                row["frequency"],
            )
        ].append(row)

    summaries: list[dict[str, Any]] = []
    median_fields = (
        "net_terminal_value",
        "gross_terminal_value",
        "net_profit",
        "gross_profit",
        "execution_cost_terminal_impact",
        "asset_quantity",
        "final_uninvested_cash",
        "explicit_fees_paid",
        "estimated_spread_cost",
        "total_execution_cost",
        "order_count",
        "average_order_size",
        "average_pending_cash_age_seconds",
        "average_cash_balance",
        "average_capital_deployment_ratio",
        "contribution_neutralized_drawdown",
        "net_difference_vs_monthly",
        "gross_timing_difference_vs_monthly",
        "differential_cost_impact_vs_monthly",
        "cash_balance_difference_vs_monthly",
    )
    for key, items in sorted(grouped.items(), key=str):
        pair, research_phase, window_set_id, overlapping, profile_id, frequency = key
        worst_window = min(items, key=lambda item: item["net_terminal_value"])
        matching_phases = [
            row
            for row in phase_rows
            if row["pair"] == pair
            and row["research_phase"] == research_phase
            and row["window_set_id"] == window_set_id
            and row["cost_profile_id"] == profile_id
            and row["frequency"] == frequency
        ]
        worst_phase = min(matching_phases, key=lambda item: item["net_terminal_value"])
        wins = sum(item["net_difference_vs_monthly"] > 0 for item in items)
        ties = sum(item["net_difference_vs_monthly"] == 0 for item in items)
        evaluated = len(items)
        row: dict[str, Any] = {
            "pair": pair,
            "research_phase": research_phase,
            "window_set_id": window_set_id,
            "overlapping": overlapping,
            "cost_profile_id": profile_id,
            "frequency": frequency,
            "windows_evaluated": evaluated,
            "win_rate_vs_monthly": (
                Decimal(wins) + Decimal(ties) / Decimal("2")
            )
            / Decimal(evaluated),
            "worst_window_timerange": worst_window["timerange"],
            "worst_window_terminal_value": worst_window["net_terminal_value"],
            "worst_phase_timerange": worst_phase["timerange"],
            "worst_phase_offset_months": worst_phase["phase_offset_months"],
            "worst_phase_terminal_value": worst_phase["net_terminal_value"],
            "median_phase_dispersion": _median(
                item["phase_dispersion"] for item in items
            )
            or Decimal("0"),
            "maximum_phase_dispersion": max(
                item["phase_dispersion"] for item in items
            ),
            "maximum_pending_cash_age_seconds": max(
                item["maximum_pending_cash_age_seconds"] for item in items
            ),
        }
        for field in median_fields:
            row[f"median_{field}"] = _median(item[field] for item in items) or Decimal("0")
        row["classification"] = _classification(row, policy)
        summaries.append(row)

    ranking_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in summaries:
        ranking_groups[
            (
                row["pair"],
                row["research_phase"],
                row["window_set_id"],
                row["cost_profile_id"],
            )
        ].append(row)
    for rows in ranking_groups.values():
        rows.sort(
            key=lambda row: (
                -row["median_net_terminal_value"],
                -row["worst_window_terminal_value"],
                row["maximum_phase_dispersion"],
                FREQUENCY_ORDER.index(row["frequency"]),
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["robust_rank"] = rank
    return sorted(
        summaries,
        key=lambda row: (
            row["cost_profile_id"],
            row["window_set_id"],
            row["robust_rank"],
        ),
    )


def aggregate_frequency_results(
    *,
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    policy: Mapping[str, Any],
    phase: str,
    result_files: Sequence[Path],
    repository_commit: str,
) -> dict[str, Any]:
    """Aggregate phases before ranking frequencies within each cost-profile window set."""
    if phase not in {"exploratory", "confirmation"}:
        raise ValueError("phase must be exploratory or confirmation")
    phase_rows = _phase_rows(
        campaign=campaign,
        registry=registry,
        phase=phase,
        result_files=result_files,
    )
    window_rows = _window_rows(phase_rows)
    summaries = _frequency_summaries(window_rows, phase_rows, policy)
    rankings = [
        {
            "pair": row["pair"],
            "research_phase": row["research_phase"],
            "window_set_id": row["window_set_id"],
            "cost_profile_id": row["cost_profile_id"],
            "robust_rank": row["robust_rank"],
            "frequency": row["frequency"],
            "median_net_terminal_value": row["median_net_terminal_value"],
            "worst_window_terminal_value": row["worst_window_terminal_value"],
            "win_rate_vs_monthly": row["win_rate_vs_monthly"],
            "maximum_phase_dispersion": row["maximum_phase_dispersion"],
            "classification": row["classification"],
        }
        for row in summaries
    ]
    cost_decomposition = [
        {
            "pair": row["pair"],
            "research_phase": row["research_phase"],
            "window_set_id": row["window_set_id"],
            "cost_profile_id": row["cost_profile_id"],
            "frequency": row["frequency"],
            "median_gross_terminal_value": row["median_gross_terminal_value"],
            "median_net_terminal_value": row["median_net_terminal_value"],
            "median_execution_cost_terminal_impact": row[
                "median_execution_cost_terminal_impact"
            ],
            "median_explicit_fees_paid": row["median_explicit_fees_paid"],
            "median_estimated_spread_cost": row["median_estimated_spread_cost"],
            "median_total_execution_cost": row["median_total_execution_cost"],
            "median_gross_timing_difference_vs_monthly": row[
                "median_gross_timing_difference_vs_monthly"
            ],
            "median_differential_cost_impact_vs_monthly": row[
                "median_differential_cost_impact_vs_monthly"
            ],
            "median_net_difference_vs_monthly": row[
                "median_net_difference_vs_monthly"
            ],
            "median_average_cash_balance": row["median_average_cash_balance"],
            "median_average_pending_cash_age_seconds": row[
                "median_average_pending_cash_age_seconds"
            ],
        }
        for row in summaries
    ]
    classifications = [
        {
            "pair": row["pair"],
            "research_phase": row["research_phase"],
            "window_set_id": row["window_set_id"],
            "cost_profile_id": row["cost_profile_id"],
            "frequency": row["frequency"],
            "classification": row["classification"],
            "robust_rank": row["robust_rank"],
            "windows_evaluated": row["windows_evaluated"],
            "win_rate_vs_monthly": row["win_rate_vs_monthly"],
            "maximum_phase_dispersion": row["maximum_phase_dispersion"],
        }
        for row in summaries
    ]
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"],
        "policy_id": policy["policy_id"],
        "repository_commit": repository_commit,
        "research_phase": phase,
        "phase_treatment": policy["phase_treatment"],
        "ranking_rule": policy["ranking_rule"],
        "ranking_metric": policy["ranking_metric"],
        "disclosures": campaign["disclosures"],
        "phase_level_results": phase_rows,
        "window_frequency_aggregates": window_rows,
        "frequency_summaries": summaries,
        "cost_decomposition": cost_decomposition,
        "rankings": rankings,
        "classification_artifact": {
            "schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "campaign_id": campaign["campaign_id"],
            "policy_id": policy["policy_id"],
            "research_phase": phase,
            "classifications": classifications,
        },
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _canonical(value)
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["frequency"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(_jsonable(value), sort_keys=True)
                    if isinstance(value, (list, tuple, dict))
                    else _jsonable(value)
                    for key, value in row.items()
                }
            )


def write_frequency_analysis_outputs(
    payload: Mapping[str, Any],
    output_dir: Path,
) -> None:
    """Write compact phase, window, frequency, cost, ranking and classification artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    serializable = _jsonable(payload)
    (output_dir / "passive-frequency-analysis.json").write_text(
        json.dumps(serializable, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        output_dir / "passive-frequency-phase-level.csv",
        payload["phase_level_results"],
    )
    _write_csv(
        output_dir / "passive-frequency-window-summary.csv",
        payload["window_frequency_aggregates"],
    )
    _write_csv(
        output_dir / "passive-frequency-summary.csv",
        payload["frequency_summaries"],
    )
    _write_csv(
        output_dir / "passive-frequency-cost-decomposition.csv",
        payload["cost_decomposition"],
    )
    _write_csv(
        output_dir / "passive-frequency-rankings.csv",
        payload["rankings"],
    )
    (output_dir / "passive-frequency-classification.json").write_text(
        json.dumps(
            _jsonable(payload["classification_artifact"]),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Passive DCA frequency robustness analysis",
        "",
        f"Research phase: **{payload['research_phase']}**",
        "",
        "> Calendar phases are equal-weight nuisance replications within the same market window; they are not independent observations and no best phase is selected.",
        "",
    ]
    summaries = payload["frequency_summaries"]
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in summaries:
        groups[(str(row["cost_profile_id"]), str(row["window_set_id"]))].append(row)
    for (profile_id, window_set_id), rows in sorted(groups.items()):
        lines.extend(
            [
                f"## `{profile_id}` · `{window_set_id}`",
                "",
                "| Rank | Frequency | Median net | Worst window | Win rate vs monthly | Gross timing Δ | Cost-impact Δ | Avg cash age | Max phase dispersion | Classification |",
                "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in sorted(rows, key=lambda item: int(item["robust_rank"])):
            lines.append(
                f"| {row['robust_rank']} | {row['frequency']} | "
                f"{_canonical(row['median_net_terminal_value'])} | "
                f"{_canonical(row['worst_window_terminal_value'])} | "
                f"{_canonical(row['win_rate_vs_monthly'])} | "
                f"{_canonical(row['median_gross_timing_difference_vs_monthly'])} | "
                f"{_canonical(row['median_differential_cost_impact_vs_monthly'])} | "
                f"{_canonical(row['median_average_pending_cash_age_seconds'])} | "
                f"{_canonical(row['maximum_phase_dispersion'])} | "
                f"{row['classification']} |"
            )
        lines.append("")
    lines.extend(
        [
            "Ranking uses phase-aggregated median terminal value, then worst-window value, then phase dispersion. It never uses the best calendar phase.",
            "",
        ]
    )
    (output_dir / "job-summary.md").write_text("\n".join(lines), encoding="utf-8")
