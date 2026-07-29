"""Validate, execute and summarize the passive DCA frequency long-horizon study."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab import passive_dca_frequency_research as base_research
from roundup_crypto_lab.dca_registry import StrategyRegistry, load_registry
from roundup_crypto_lab.passive_dca_frequency_analysis import (
    aggregate_frequency_results,
    write_frequency_analysis_outputs,
)
from roundup_crypto_lab.passive_dca_frequency_campaign import (
    EXPECTED_PHASES,
    FREQUENCY_ORDER,
    aggregate_coverage,
    load_json,
    plan_campaign,
    validate_frequency_registry,
    write_coverage_outputs,
)

STUDY_SCHEMA_VERSION = "passive-dca-frequency-long-horizon/v1"
ANALYSIS_SCHEMA_VERSION = "passive-dca-frequency-long-horizon-analysis/v1"
CONCLUSION_SCHEMA_VERSION = "passive-dca-frequency-long-horizon-conclusion/v1"
EXPECTED_STUDY_KEYS = {
    "schema_version",
    "study_id",
    "scientific_role",
    "research_path",
    "timerange",
    "window_set_id",
    "analysis_phase",
    "primary_benchmark_strategy_id",
    "expected_scenario_count",
    "expected_strategy_result_count",
    "trajectory_sampling",
    "disclosures",
}
EXPECTED_TIMERANGE = "20180701-20260101"
EXPECTED_MONTHS = 90
EXPECTED_SCENARIOS = 4
EXPECTED_STRATEGY_RESULTS = 28
BENCHMARK_STRATEGY_ID = "monthly-dca"
BENCHMARK_FREQUENCY = "monthly"
KNOWN_GAP_END = datetime(2018, 1, 13, 8, tzinfo=UTC)
TRAJECTORY_FIELDS = (
    "cash_balance",
    "crypto_value",
    "portfolio_value",
    "cumulative_contributions",
    "capital_invested",
    "cumulative_fees_paid",
)


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    return [_identifier(item, f"{name}[]") for item in value]


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


def _median(values: Iterable[Decimal]) -> Decimal:
    materialized = list(values)
    if not materialized:
        raise ValueError("median requires at least one value")
    return statistics.median(materialized)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("trajectory timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("trajectory timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_timerange(timerange: str) -> tuple[datetime, datetime]:
    if len(timerange) != 17 or timerange[8] != "-":
        raise ValueError("timerange must use YYYYMMDD-YYYYMMDD")
    try:
        start = datetime.strptime(timerange[:8], "%Y%m%d").replace(tzinfo=UTC)
        end = datetime.strptime(timerange[9:], "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("timerange must contain valid dates") from exc
    if start >= end:
        raise ValueError("timerange start must precede end")
    return start, end


def _month_distance(start: datetime, end: datetime) -> int:
    if start.day != 1 or end.day != 1:
        raise ValueError("long-horizon boundaries must be first-of-month dates")
    return (end.year - start.year) * 12 + end.month - start.month


def load_long_horizon_study(path: Path) -> dict[str, Any]:
    """Load and strictly validate the committed longitudinal study definition."""
    payload = load_json(path)
    missing = sorted(EXPECTED_STUDY_KEYS - set(payload))
    extra = sorted(set(payload) - EXPECTED_STUDY_KEYS)
    if missing:
        raise ValueError(f"long-horizon study is missing keys: {', '.join(missing)}")
    if extra:
        raise ValueError(f"long-horizon study has unsupported keys: {', '.join(extra)}")
    if payload.get("schema_version") != STUDY_SCHEMA_VERSION:
        raise ValueError(f"long-horizon schema must be {STUDY_SCHEMA_VERSION}")
    _identifier(payload.get("study_id"), "study_id")
    if payload.get("scientific_role") != "historical_longitudinal_complement":
        raise ValueError("scientific_role must remain historical_longitudinal_complement")
    if payload.get("research_path") != "config/passive-dca-frequency-research.json":
        raise ValueError("research_path must use the frozen final frequency research protocol")
    if payload.get("timerange") != EXPECTED_TIMERANGE:
        raise ValueError(f"timerange must remain {EXPECTED_TIMERANGE}")
    start, end = _parse_timerange(str(payload["timerange"]))
    if start <= KNOWN_GAP_END:
        raise ValueError("long-horizon timerange must begin after the documented Kraken gap")
    if _month_distance(start, end) != EXPECTED_MONTHS:
        raise ValueError(f"long-horizon timerange must contain {EXPECTED_MONTHS} months")
    _identifier(payload.get("window_set_id"), "window_set_id")
    if payload.get("analysis_phase") != "exploratory":
        raise ValueError("analysis_phase must remain exploratory for existing artifact compatibility")
    if payload.get("primary_benchmark_strategy_id") != BENCHMARK_STRATEGY_ID:
        raise ValueError("primary benchmark must remain monthly-dca")
    if payload.get("expected_scenario_count") != EXPECTED_SCENARIOS:
        raise ValueError(f"expected_scenario_count must remain {EXPECTED_SCENARIOS}")
    if payload.get("expected_strategy_result_count") != EXPECTED_STRATEGY_RESULTS:
        raise ValueError(
            f"expected_strategy_result_count must remain {EXPECTED_STRATEGY_RESULTS}"
        )
    if payload.get("trajectory_sampling") != "month_end":
        raise ValueError("trajectory_sampling must remain month_end")
    _string_list(payload.get("disclosures"), "disclosures")
    return payload


def materialize_long_horizon_campaign(
    study: Mapping[str, Any],
    research: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay one continuous 90-month path on the frozen four-profile campaign."""
    campaign = deepcopy(base_research.materialize_research_campaign(research))
    campaign["campaign_id"] = str(study["study_id"])
    start, end = str(study["timerange"]).split("-", maxsplit=1)
    campaign["window_sets"] = [
        {
            "window_set_id": study["window_set_id"],
            "phase": study["analysis_phase"],
            "start": start,
            "end": end,
            "months": EXPECTED_MONTHS,
            "step_months": EXPECTED_MONTHS,
            "overlapping": False,
        }
    ]
    campaign["disclosures"] = list(campaign.get("disclosures", [])) + list(
        study["disclosures"]
    )
    return campaign


def validate_long_horizon_protocol(
    study: Mapping[str, Any],
    research: Mapping[str, Any],
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    policy: Mapping[str, Any],
    *,
    cost_profile_dir: Path,
) -> dict[str, Any]:
    """Validate the exact path, frozen strategies, profiles and expected matrix."""
    frequencies = validate_frequency_registry(registry)
    profiles = base_research._validate_profile_roles(
        research,
        cost_profile_dir=cost_profile_dir,
    )
    if tuple(campaign.get("cost_profiles", [])) != base_research.RESEARCH_PROFILE_IDS:
        raise ValueError("long-horizon campaign must use the four frozen research profiles")
    if policy.get("primary_control") != "MonthlyDCA":
        raise ValueError("long-horizon policy must keep MonthlyDCA as primary control")
    if policy.get("phase_treatment") != "equal_weight_nuisance_replications":
        raise ValueError("long-horizon phases must remain equal-weight nuisance replications")
    if policy.get("ranking_rule") != "frequency_aggregates_only_never_best_phase":
        raise ValueError("long-horizon policy must forbid best-phase selection")

    rows = plan_campaign(campaign)
    if len(rows) != EXPECTED_SCENARIOS:
        raise ValueError(f"long-horizon plan must contain {EXPECTED_SCENARIOS} scenarios")
    if len(rows) * len(frequencies) != EXPECTED_STRATEGY_RESULTS:
        raise ValueError(
            f"long-horizon plan must contain {EXPECTED_STRATEGY_RESULTS} strategy results"
        )
    if {row["timerange"] for row in rows} != {EXPECTED_TIMERANGE}:
        raise ValueError("long-horizon plan drifted from the frozen timerange")
    if {row["window_set_id"] for row in rows} != {study["window_set_id"]}:
        raise ValueError("long-horizon plan drifted from the frozen window set")
    if {row["phase"] for row in rows} != {study["analysis_phase"]}:
        raise ValueError("long-horizon plan drifted from the compatibility phase")
    if {row["cost_profile_id"] for row in rows} != set(
        base_research.RESEARCH_PROFILE_IDS
    ):
        raise ValueError("long-horizon plan does not contain every frozen cost profile")
    return {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": study["study_id"],
        "scientific_role": study["scientific_role"],
        "timerange": study["timerange"],
        "months": EXPECTED_MONTHS,
        "benchmark_strategy_id": BENCHMARK_STRATEGY_ID,
        "scenario_count": len(rows),
        "strategy_result_count": len(rows) * len(frequencies),
        "cost_profiles": [profile.cost_profile_id for profile in profiles],
        "frequencies": frequencies,
        "disclosures": list(study["disclosures"]),
    }


def _equity_curve(result: Mapping[str, Any]) -> dict[datetime, dict[str, Decimal]]:
    rows = result.get("equity_curve")
    if not isinstance(rows, list) or not rows:
        raise ValueError("long-horizon result must contain a non-empty equity_curve")
    curve: dict[datetime, dict[str, Decimal]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("equity_curve rows must be objects")
        timestamp = _parse_timestamp(row.get("timestamp"))
        if timestamp in curve:
            raise ValueError("equity_curve contains duplicate timestamps")
        curve[timestamp] = {
            field: _decimal(row.get(field), field) for field in TRAJECTORY_FIELDS
        }
    return curve


def _aggregate_phase_trajectories(
    results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    curves = [_equity_curve(result) for result in results]
    timestamps = set(curves[0])
    for curve in curves[1:]:
        if set(curve) != timestamps:
            raise ValueError("frequency nuisance phases must share identical trajectory timestamps")
    aggregated = []
    for timestamp in sorted(timestamps):
        row: dict[str, Any] = {"timestamp": timestamp}
        for field in TRAJECTORY_FIELDS:
            row[field] = _median(curve[timestamp][field] for curve in curves)
        contributions = row["cumulative_contributions"]
        row["capital_deployment_ratio"] = (
            Decimal("0")
            if contributions == 0
            else row["capital_invested"] / contributions
        )
        aggregated.append(row)
    return aggregated


def _maximum_consecutive_days_below(
    rows: Sequence[Mapping[str, Any]],
) -> Decimal:
    if len(rows) < 2:
        return Decimal("0")
    intervals = [
        (rows[index]["timestamp"] - rows[index - 1]["timestamp"]).total_seconds()
        for index in range(1, len(rows))
    ]
    interval_seconds = Decimal(str(statistics.median(intervals)))
    current = 0
    maximum = 0
    for row in rows:
        if row["difference_vs_monthly"] < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return Decimal(maximum) * interval_seconds / Decimal("86400")


def _trajectory_metrics(
    frequency: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Decimal | int]:
    if not rows:
        raise ValueError("trajectory metrics require rows")
    differences = [row["difference_vs_monthly"] for row in rows]
    below = sum(value < 0 for value in differences)
    ratios = [row["capital_deployment_ratio"] for row in rows]
    cash = [row["cash_balance"] for row in rows]
    return {
        "observation_count": len(rows),
        "observations_below_monthly": below,
        "time_below_monthly_ratio": Decimal(below) / Decimal(len(rows)),
        "maximum_consecutive_days_below_monthly": _maximum_consecutive_days_below(rows),
        "worst_intermediate_difference_vs_monthly": min(differences),
        "best_intermediate_difference_vs_monthly": max(differences),
        "average_capital_deployment_ratio": sum(ratios, Decimal("0"))
        / Decimal(len(ratios)),
        "average_cash_balance": sum(cash, Decimal("0")) / Decimal(len(cash)),
        "ending_difference_vs_monthly": differences[-1],
        "frequency_is_benchmark": int(frequency == BENCHMARK_FREQUENCY),
    }


def _month_end_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    selected: dict[tuple[int, int], Mapping[str, Any]] = {}
    for row in rows:
        timestamp = row["timestamp"]
        selected[(timestamp.year, timestamp.month)] = row
    return [selected[key] for key in sorted(selected)]


def _result_final_metrics(results: Sequence[Mapping[str, Any]]) -> dict[str, Decimal]:
    values = [_decimal(result.get("final_value_exact"), "final_value_exact") for result in results]
    quantities = [_decimal(result.get("quantity_exact"), "quantity_exact") for result in results]
    cash = [_decimal(result.get("cash_balance_exact"), "cash_balance_exact") for result in results]
    costs = [
        _decimal(
            result.get("execution_costs", {}).get("total_execution_cost"),
            "total_execution_cost",
        )
        for result in results
    ]
    orders = [
        _decimal(result.get("execution_costs", {}).get("order_count"), "order_count")
        for result in results
    ]
    center = _median(values)
    return {
        "final_net_terminal_value": center,
        "final_btc_quantity": _median(quantities),
        "final_cash_balance": _median(cash),
        "total_execution_cost": _median(costs),
        "median_order_count": _median(orders),
        "phase_terminal_dispersion": (
            Decimal("0")
            if len(values) == 1
            else (max(values) - min(values)) / abs(center)
            if center != 0
            else max(values) - min(values)
        ),
    }


def build_long_horizon_analysis(
    *,
    study: Mapping[str, Any],
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    result_files: Sequence[Path],
    repository_commit: str,
) -> dict[str, Any]:
    """Build phase-aggregated terminal and path diagnostics for all four profiles."""
    planned = {row["scenario_id"]: row for row in plan_campaign(campaign)}
    payloads: dict[str, Mapping[str, Any]] = {}
    for source in sorted(result_files):
        payload = load_json(source)
        scenario_id = str(payload.get("campaign", {}).get("scenario_id", ""))
        if scenario_id not in planned:
            raise ValueError(f"unplanned long-horizon scenario artifact: {source}")
        if scenario_id in payloads:
            raise ValueError(f"duplicate long-horizon scenario artifact: {scenario_id}")
        if payload.get("registry", {}).get("registry_digest") != registry.digest:
            raise ValueError(f"long-horizon registry digest mismatch: {source}")
        if payload.get("scenario", {}).get("timerange") != EXPECTED_TIMERANGE:
            raise ValueError(f"long-horizon timerange mismatch: {source}")
        payloads[scenario_id] = payload
    missing = sorted(set(planned) - set(payloads))
    if missing:
        raise ValueError(f"long-horizon analysis is missing scenarios: {missing}")

    summaries: list[dict[str, Any]] = []
    trajectory_rows: list[dict[str, Any]] = []
    winners: dict[str, str] = {}
    for profile_id in base_research.RESEARCH_PROFILE_IDS:
        scenario_id = next(
            scenario_id
            for scenario_id, row in planned.items()
            if row["cost_profile_id"] == profile_id
        )
        payload = payloads[scenario_id]
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError("long-horizon scenario results must be an array")
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        phases: dict[str, set[int | None]] = defaultdict(set)
        for result in results:
            if not isinstance(result, Mapping):
                raise ValueError("long-horizon frequency result must be an object")
            frequency = result.get("frequency", {})
            frequency_id = str(frequency.get("frequency", ""))
            if frequency_id not in EXPECTED_PHASES:
                raise ValueError(f"unexpected long-horizon frequency: {frequency_id}")
            grouped[frequency_id].append(result)
            phases[frequency_id].add(frequency.get("phase_offset_months"))
        if set(grouped) != set(FREQUENCY_ORDER):
            raise ValueError("long-horizon scenario does not contain every frequency")
        for frequency in FREQUENCY_ORDER:
            if phases[frequency] != set(EXPECTED_PHASES[frequency]):
                raise ValueError(f"incomplete nuisance phases for long-horizon {frequency}")

        aggregated = {
            frequency: _aggregate_phase_trajectories(grouped[frequency])
            for frequency in FREQUENCY_ORDER
        }
        monthly_by_timestamp = {
            row["timestamp"]: row for row in aggregated[BENCHMARK_FREQUENCY]
        }
        monthly_final = _result_final_metrics(grouped[BENCHMARK_FREQUENCY])[
            "final_net_terminal_value"
        ]
        profile_summaries: list[dict[str, Any]] = []
        for frequency in FREQUENCY_ORDER:
            enriched = []
            for row in aggregated[frequency]:
                monthly = monthly_by_timestamp.get(row["timestamp"])
                if monthly is None:
                    raise ValueError("MonthlyDCA trajectory is missing a peer timestamp")
                enriched.append(
                    {
                        **row,
                        "difference_vs_monthly": (
                            row["portfolio_value"] - monthly["portfolio_value"]
                        ),
                    }
                )
            metrics = {
                **_result_final_metrics(grouped[frequency]),
                **_trajectory_metrics(frequency, enriched),
            }
            metrics["final_difference_vs_monthly"] = (
                metrics["final_net_terminal_value"] - monthly_final
            )
            summary = {
                "cost_profile_id": profile_id,
                "frequency": frequency,
                "phase_count": len(grouped[frequency]),
                **metrics,
            }
            profile_summaries.append(summary)
            for row in _month_end_rows(enriched):
                trajectory_rows.append(
                    {
                        "cost_profile_id": profile_id,
                        "frequency": frequency,
                        "timestamp": row["timestamp"].isoformat(),
                        "portfolio_value": row["portfolio_value"],
                        "monthly_portfolio_value": monthly_by_timestamp[
                            row["timestamp"]
                        ]["portfolio_value"],
                        "difference_vs_monthly": row["difference_vs_monthly"],
                        "cash_balance": row["cash_balance"],
                        "crypto_value": row["crypto_value"],
                        "capital_invested": row["capital_invested"],
                        "cumulative_contributions": row["cumulative_contributions"],
                        "capital_deployment_ratio": row[
                            "capital_deployment_ratio"
                        ],
                    }
                )
        profile_summaries.sort(
            key=lambda row: (
                -row["final_net_terminal_value"],
                FREQUENCY_ORDER.index(row["frequency"]),
            )
        )
        for rank, row in enumerate(profile_summaries, start=1):
            row["final_rank"] = rank
        winners[profile_id] = str(profile_summaries[0]["frequency"])
        summaries.extend(profile_summaries)

    all_monthly = set(winners.values()) == {BENCHMARK_FREQUENCY}
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "study_id": study["study_id"],
        "scientific_role": study["scientific_role"],
        "repository_commit": repository_commit,
        "timerange": study["timerange"],
        "months": EXPECTED_MONTHS,
        "benchmark_strategy_id": BENCHMARK_STRATEGY_ID,
        "phase_treatment": "equal_weight_nuisance_replications_never_best_phase",
        "profile_winners": winners,
        "historical_consistency_status": (
            "consistent_with_confirmed_monthly_dca"
            if all_monthly
            else "mixed_historical_evidence"
        ),
        "frequency_summaries": summaries,
        "monthly_trajectory": trajectory_rows,
        "disclosures": list(study["disclosures"]),
    }


def build_long_horizon_conclusion(
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """State historical consistency without treating the observed path as a holdout."""
    winners = dict(analysis["profile_winners"])
    consistent = set(winners.values()) == {BENCHMARK_FREQUENCY}
    return {
        "schema_version": CONCLUSION_SCHEMA_VERSION,
        "study_id": analysis["study_id"],
        "scientific_role": analysis["scientific_role"],
        "timerange": analysis["timerange"],
        "historical_consistency_status": analysis["historical_consistency_status"],
        "profile_winners": winners,
        "official_decision_effect": "informational_only_no_automatic_decision_change",
        "monthly_dca_consistent_across_all_profiles": consistent,
        "independent_confirmation": False,
        "requires_human_review": True,
        "interpretation": (
            "The continuous historical path is consistent with the confirmed MonthlyDCA decision."
            if consistent
            else "The continuous historical path contains mixed evidence and must be reviewed against the existing multi-window research."
        ),
        "limitations": [
            "The path was observed during prior research and is not an independent holdout.",
            "One continuous path cannot measure start-date robustness by itself.",
            "The result applies to BTC/EUR, the committed 4h data and frozen cost assumptions.",
            "Historical consistency does not guarantee 10- to 15-year future performance.",
        ],
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _canonical(value)
    if isinstance(value, datetime):
        return value.isoformat()
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
            writer.writerow({key: _jsonable(value) for key, value in row.items()})


def write_long_horizon_outputs(
    analysis: Mapping[str, Any],
    conclusion: Mapping[str, Any],
    output_dir: Path,
) -> None:
    """Write compact machine-readable, tabular and GitHub summary outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "passive-frequency-long-horizon-analysis.json").write_text(
        json.dumps(_jsonable(analysis), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "passive-frequency-long-horizon-conclusion.json").write_text(
        json.dumps(_jsonable(conclusion), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        output_dir / "passive-frequency-long-horizon-summary.csv",
        analysis["frequency_summaries"],
    )
    _write_csv(
        output_dir / "passive-frequency-long-horizon-trajectory.csv",
        analysis["monthly_trajectory"],
    )

    lines = [
        "# Passive DCA frequency long-horizon validation",
        "",
        f"Timerange: **{analysis['timerange']}** ({analysis['months']} months)",
        f"Scientific role: **{analysis['scientific_role']}**",
        f"Historical consistency: **{analysis['historical_consistency_status']}**",
        "",
        "> This is a historical longitudinal complement, not an independent confirmation and not a reopening of frequency optimization.",
        "",
    ]
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in analysis["frequency_summaries"]:
        grouped[str(row["cost_profile_id"])].append(row)
    for profile_id, rows in grouped.items():
        lines.extend(
            [
                f"## `{profile_id}`",
                "",
                "| Rank | Frequency | Final net | Δ vs monthly | BTC | Final cash | Time below monthly | Worst path Δ | Max consecutive days below | Avg deployment | Cost |",
                "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in sorted(rows, key=lambda item: int(item["final_rank"])):
            lines.append(
                f"| {row['final_rank']} | {row['frequency']} | "
                f"{_canonical(row['final_net_terminal_value'])} | "
                f"{_canonical(row['final_difference_vs_monthly'])} | "
                f"{_canonical(row['final_btc_quantity'])} | "
                f"{_canonical(row['final_cash_balance'])} | "
                f"{_canonical(row['time_below_monthly_ratio'])} | "
                f"{_canonical(row['worst_intermediate_difference_vs_monthly'])} | "
                f"{_canonical(row['maximum_consecutive_days_below_monthly'])} | "
                f"{_canonical(row['average_capital_deployment_ratio'])} | "
                f"{_canonical(row['total_execution_cost'])} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            str(conclusion["interpretation"]),
            "",
            "The trajectory CSV samples the final 4h observation of each calendar month. Path diagnostics use every 4h observation.",
            "",
        ]
    )
    (output_dir / "long-horizon-summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _load_inputs(
    study_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    StrategyRegistry,
    dict[str, Any],
    Path,
]:
    study = load_long_horizon_study(study_path)
    research = base_research.load_research_protocol(Path(str(study["research_path"])))
    campaign = materialize_long_horizon_campaign(study, research)
    registry = load_registry(Path(str(research["registry_path"])))
    policy = load_json(Path(str(research["policy_path"])))
    cost_profile_dir = Path(str(research["cost_profile_dir"]))
    return study, research, campaign, registry, policy, cost_profile_dir


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--output", type=Path, required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    study, research, campaign, registry, policy, cost_profile_dir = _load_inputs(
        args.study
    )
    validation = validate_long_horizon_protocol(
        study,
        research,
        campaign,
        registry,
        policy,
        cost_profile_dir=cost_profile_dir,
    )
    if args.command == "validate":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        return
    if args.command == "plan":
        rows = plan_campaign(campaign)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return

    result_files = sorted(args.results_dir.rglob("frequency-scenario.json"))
    if not result_files:
        raise ValueError("long-horizon study found no scenario artifacts")
    coverage = aggregate_coverage(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase=str(study["analysis_phase"]),
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    if coverage["missing_scenario_ids"]:
        raise ValueError(
            f"long-horizon campaign is incomplete: {len(coverage['missing_scenario_ids'])} missing"
        )
    write_coverage_outputs(coverage, args.output_dir / "coverage")

    generic_analysis = aggregate_frequency_results(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase=str(study["analysis_phase"]),
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    write_frequency_analysis_outputs(generic_analysis, args.output_dir / "analysis")

    longitudinal = build_long_horizon_analysis(
        study=study,
        campaign=campaign,
        registry=registry,
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    conclusion = build_long_horizon_conclusion(longitudinal)
    write_long_horizon_outputs(longitudinal, conclusion, args.output_dir / "longitudinal")
    (args.output_dir / "resolved-long-horizon-study.json").write_text(
        json.dumps(study, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "resolved-long-horizon-campaign.json").write_text(
        json.dumps(campaign, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
