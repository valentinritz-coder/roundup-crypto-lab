"""Validate, plan and index passive DCA frequency campaign runs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roundup_crypto_lab.dca_costed_execution import registered_frequency_strategies
from roundup_crypto_lab.dca_registry import StrategyDefinition, StrategyRegistry, load_registry
from roundup_crypto_lab.deployment_engine import WEEKDAYS
from roundup_crypto_lab.execution_costs import (
    DEFAULT_COST_PROFILE_DIR,
    ExecutionCostProfile,
    resolve_cost_profile,
)

CAMPAIGN_SCHEMA_VERSION = "passive-dca-frequency-campaign/v1"
POLICY_SCHEMA_VERSION = "passive-dca-frequency-policy/v1"
COVERAGE_SCHEMA_VERSION = "passive-dca-frequency-coverage/v1"
REQUIRED_PROFILE_IDS = (
    "frictionless-control-v1",
    "proportional-fee-v1",
    "proportional-plus-spread-v1",
)
FREQUENCY_ORDER = ("weekly", "monthly", "every-2-months", "quarterly")
EXPECTED_PHASES = {
    "weekly": (None,),
    "monthly": (0,),
    "every-2-months": (0, 1),
    "quarterly": (0, 1, 2),
}


def load_json(path: Path) -> dict[str, Any]:
    """Load one JSON object and reject non-object roots."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _month_add(value: datetime, months: int) -> datetime:
    total = value.year * 12 + value.month - 1 + months
    year, month = divmod(total, 12)
    return value.replace(year=year, month=month + 1, day=1)


def _parse_day(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must use YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{name} must use a valid YYYYMMDD date") from exc


def frequency_metadata(definition: StrategyDefinition) -> dict[str, Any]:
    """Return campaign grouping metadata for one passive strategy definition."""
    if definition.required_indicators:
        raise ValueError(f"{definition.strategy_id} must not require indicators")
    if definition.implementation == "fixed_weekly":
        if dict(definition.parameters) != {"weekday": 0}:
            raise ValueError("weekly frequency must use the preregistered Monday convention")
        return {
            "frequency": "weekly",
            "interval_months": None,
            "phase_offset_months": None,
            "phase_path": "phase-predefined-weekday",
        }
    if definition.implementation == "fixed_monthly":
        if definition.parameters:
            raise ValueError("monthly frequency must not define search parameters")
        return {
            "frequency": "monthly",
            "interval_months": 1,
            "phase_offset_months": 0,
            "phase_path": "phase-0",
        }
    if definition.implementation != "fixed_periodic":
        raise ValueError(
            "unsupported passive frequency implementation: "
            f"{definition.implementation}"
        )
    interval = definition.parameters.get("interval_months")
    phase = definition.parameters.get("phase_offset_months")
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise ValueError("interval_months must be an integer")
    if isinstance(phase, bool) or not isinstance(phase, int):
        raise ValueError("phase_offset_months must be an integer")
    frequency = {2: "every-2-months", 3: "quarterly"}.get(interval)
    if frequency is None or phase not in EXPECTED_PHASES[frequency]:
        raise ValueError("periodic frequency must be every two months or quarterly")
    return {
        "frequency": frequency,
        "interval_months": interval,
        "phase_offset_months": phase,
        "phase_path": f"phase-{phase}",
    }


def validate_frequency_registry(registry: StrategyRegistry) -> list[dict[str, Any]]:
    """Require a dedicated registry containing only the complete passive matrix."""
    if not isinstance(registry, StrategyRegistry):
        raise TypeError("registry must be a StrategyRegistry")
    allowed = {"fixed_weekly", "fixed_monthly", "fixed_periodic"}
    unexpected = [
        definition.strategy_id
        for definition in registry.strategies
        if definition.implementation not in allowed
    ]
    if unexpected:
        raise ValueError(
            "passive frequency registry contains non-passive strategies: "
            + ", ".join(sorted(unexpected))
        )
    definitions = registered_frequency_strategies(registry)
    metadata = []
    identities: set[tuple[str, int | None]] = set()
    for definition in definitions:
        item = frequency_metadata(definition)
        identity = (item["frequency"], item["phase_offset_months"])
        if identity in identities:
            raise ValueError(f"duplicate passive frequency identity: {identity}")
        identities.add(identity)
        metadata.append({"strategy_id": definition.strategy_id, **item})
    expected = {
        (frequency, phase)
        for frequency, phases in EXPECTED_PHASES.items()
        for phase in phases
    }
    if identities != expected:
        missing = sorted(expected - identities, key=str)
        extra = sorted(identities - expected, key=str)
        raise ValueError(f"incomplete passive frequency matrix; missing={missing}, extra={extra}")
    return metadata


def _validate_policy(policy: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ValueError(f"policy schema must be {POLICY_SCHEMA_VERSION}")
    _identifier(policy.get("policy_id"), "policy_id")
    if policy.get("primary_control") != "MonthlyDCA":
        raise ValueError("passive frequency policy must keep MonthlyDCA as primary control")
    if policy.get("phase_treatment") != "equal_weight_nuisance_replications":
        raise ValueError("phase offsets must be equal-weight nuisance replications")
    if policy.get("ranking_rule") != "frequency_aggregates_only_never_best_phase":
        raise ValueError("policy must forbid best-phase ranking")
    required = set(policy.get("required_cost_components", []))
    expected = {
        "explicit_fees_paid",
        "estimated_spread_cost",
        "total_execution_cost",
        "final_uninvested_cash",
        "order_count",
        "average_order_size",
    }
    if required != expected:
        raise ValueError("policy required cost components are incomplete")


def _validate_windows(campaign: Mapping[str, Any]) -> None:
    seen: set[str] = set()
    phase_ranges: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    windows = campaign.get("window_sets")
    if not isinstance(windows, list) or not windows:
        raise ValueError("campaign must define window_sets")
    for window in windows:
        if not isinstance(window, Mapping):
            raise ValueError("window sets must be objects")
        window_id = _identifier(window.get("window_set_id"), "window_set_id")
        if window_id in seen:
            raise ValueError(f"duplicate window_set_id: {window_id}")
        seen.add(window_id)
        phase = window.get("phase")
        if phase not in {"exploratory", "confirmation"}:
            raise ValueError("window phase must be exploratory or confirmation")
        start = _parse_day(window.get("start"), f"{window_id}.start")
        end = _parse_day(window.get("end"), f"{window_id}.end")
        months = window.get("months")
        step = window.get("step_months")
        if isinstance(months, bool) or not isinstance(months, int) or months <= 0:
            raise ValueError(f"{window_id}.months must be a positive integer")
        if isinstance(step, bool) or not isinstance(step, int) or step <= 0:
            raise ValueError(f"{window_id}.step_months must be a positive integer")
        if start >= end or _month_add(start, months) > end:
            raise ValueError(f"{window_id} does not contain a complete research window")
        if bool(window.get("overlapping")) != (step < months):
            raise ValueError(f"{window_id}.overlapping must match step_months < months")
        phase_ranges[str(phase)].append((start, end))
    if not phase_ranges.get("exploratory") or not phase_ranges.get("confirmation"):
        raise ValueError("campaign must define exploratory and confirmation windows")
    exploratory_end = max(end for _, end in phase_ranges["exploratory"])
    confirmation_start = min(start for start, _ in phase_ranges["confirmation"])
    if exploratory_end > confirmation_start:
        raise ValueError("exploratory and confirmation date ranges must not overlap")


def resolve_campaign_profiles(
    campaign: Mapping[str, Any],
    *,
    cost_profile_dir: Path = DEFAULT_COST_PROFILE_DIR,
) -> list[ExecutionCostProfile]:
    references = campaign.get("cost_profiles")
    if not isinstance(references, list) or not references:
        raise ValueError("campaign must define cost_profiles")
    if tuple(references) != REQUIRED_PROFILE_IDS:
        raise ValueError(
            "campaign cost profiles must be frictionless, fee-only and proportional-plus-spread"
        )
    profiles = [
        resolve_cost_profile(reference, search_dir=cost_profile_dir)
        for reference in references
    ]
    if tuple(profile.cost_profile_id for profile in profiles) != REQUIRED_PROFILE_IDS:
        raise ValueError("resolved cost profile identities do not match campaign configuration")
    if len({profile.digest for profile in profiles}) != len(profiles):
        raise ValueError("campaign cost profiles must have distinct digests")
    return profiles


def validate_campaign(
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    policy: Mapping[str, Any],
    *,
    cost_profile_dir: Path = DEFAULT_COST_PROFILE_DIR,
) -> dict[str, Any]:
    """Validate all committed inputs before any shard is dispatched."""
    if campaign.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
        raise ValueError(f"campaign schema must be {CAMPAIGN_SCHEMA_VERSION}")
    campaign_id = _identifier(campaign.get("campaign_id"), "campaign_id")
    if campaign.get("timeframe") != "4h":
        raise ValueError("passive frequency campaign supports only the prepared 4h timeframe")
    if campaign.get("pairs") != ["BTC/EUR"]:
        raise ValueError("initial passive frequency campaign must contain only BTC/EUR")
    plan = campaign.get("investment_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("investment_plan must be an object")
    weekly_day = plan.get("weekly_day")
    if weekly_day not in WEEKDAYS or weekly_day != "monday":
        raise ValueError("weekly_day must remain monday")
    contribution_day = plan.get("contribution_day")
    if (
        isinstance(contribution_day, bool)
        or not isinstance(contribution_day, int)
        or not 1 <= contribution_day <= 31
    ):
        raise ValueError("contribution_day must be an integer from 1 through 31")
    for name in ("initial_capital", "monthly_budget"):
        value = plan.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"investment_plan.{name} must be a decimal string")
    if campaign.get("registry_path") != "config/passive-dca-frequency-strategies.json":
        raise ValueError("campaign must use the dedicated passive frequency registry")
    if campaign.get("policy_path") != "config/passive-dca-frequency-policy.json":
        raise ValueError("campaign must use the dedicated passive frequency policy")
    _validate_windows(campaign)
    _validate_policy(policy)
    frequencies = validate_frequency_registry(registry)
    profiles = resolve_campaign_profiles(campaign, cost_profile_dir=cost_profile_dir)
    plans = plan_campaign(campaign)
    by_phase = {
        phase: sum(row["phase"] == phase for row in plans)
        for phase in ("exploratory", "confirmation")
    }
    return {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "registry_id": registry.registry_id,
        "registry_digest": registry.digest,
        "policy_id": policy["policy_id"],
        "cost_profiles": [profile.artifact() for profile in profiles],
        "frequencies": frequencies,
        "scenario_counts": by_phase,
    }


def plan_campaign(campaign: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Materialize deterministic window × cost-profile scenarios."""
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    investment_plan = campaign["investment_plan"]
    for window in campaign["window_sets"]:
        cursor = _parse_day(window["start"], f"{window['window_set_id']}.start")
        limit = _parse_day(window["end"], f"{window['window_set_id']}.end")
        months = int(window["months"])
        step = int(window["step_months"])
        while _month_add(cursor, months) <= limit:
            end = _month_add(cursor, months)
            timerange = f"{cursor:%Y%m%d}-{end:%Y%m%d}"
            for pair in campaign["pairs"]:
                for profile_id in campaign["cost_profiles"]:
                    scenario_id = (
                        f"{window['phase']}::{profile_id}::"
                        f"{window['window_set_id']}::{pair.replace('/', '_')}::{timerange}"
                    )
                    if scenario_id in identities:
                        raise ValueError(f"duplicate passive frequency scenario: {scenario_id}")
                    identities.add(scenario_id)
                    rows.append(
                        {
                            "scenario_id": scenario_id,
                            "campaign_id": campaign["campaign_id"],
                            "pair": pair,
                            "timeframe": campaign["timeframe"],
                            "window_set_id": window["window_set_id"],
                            "phase": window["phase"],
                            "timerange": timerange,
                            "months": months,
                            "step_months": step,
                            "overlapping": bool(window["overlapping"]),
                            "cost_profile_id": profile_id,
                            "registry_path": campaign["registry_path"],
                            "cost_profile_dir": campaign["cost_profile_dir"],
                            "initial_capital": investment_plan["initial_capital"],
                            "monthly_budget": investment_plan["monthly_budget"],
                            "contribution_day": investment_plan["contribution_day"],
                            "weekly_day": investment_plan["weekly_day"],
                        }
                    )
            cursor = _month_add(cursor, step)
    return rows


def aggregate_coverage(
    *,
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    policy: Mapping[str, Any],
    phase: str,
    result_files: Sequence[Path],
    repository_commit: str,
) -> dict[str, Any]:
    """Index completed scenario artifacts without ranking frequencies or phases."""
    if phase not in {"exploratory", "confirmation"}:
        raise ValueError("phase must be exploratory or confirmation")
    expected_rows = [row for row in plan_campaign(campaign) if row["phase"] == phase]
    expected = {row["scenario_id"]: row for row in expected_rows}
    frequency_definitions = {
        definition.strategy_id: frequency_metadata(definition)
        for definition in registered_frequency_strategies(registry)
    }
    observed: set[str] = set()
    inventory: list[dict[str, Any]] = []
    profile_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"scenarios": 0, "frequency_results": 0}
    )
    observed_phases: dict[str, set[int | None]] = defaultdict(set)

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
        profile_id = payload.get("execution_cost_profile", {}).get("cost_profile_id")
        if profile_id != expected[scenario_id]["cost_profile_id"]:
            raise ValueError(f"scenario cost profile mismatch: {source}")
        observed.add(scenario_id)
        profile_counts[str(profile_id)]["scenarios"] += 1

        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError(f"scenario results must be an array: {source}")
        result_ids: set[str] = set()
        for result in results:
            strategy = result.get("strategy", {})
            strategy_id = str(strategy.get("strategy_id", ""))
            if strategy_id not in frequency_definitions:
                raise ValueError(f"unexpected strategy in frequency scenario: {strategy_id}")
            if strategy_id in result_ids:
                raise ValueError(f"duplicate strategy result in scenario: {strategy_id}")
            result_ids.add(strategy_id)
            frequency = frequency_definitions[strategy_id]
            observed_phases[frequency["frequency"]].add(
                frequency["phase_offset_months"]
            )
            profile_counts[str(profile_id)]["frequency_results"] += 1
            inventory.append(
                {
                    "scenario_id": scenario_id,
                    "phase": phase,
                    "pair": expected[scenario_id]["pair"],
                    "window_set_id": expected[scenario_id]["window_set_id"],
                    "timerange": expected[scenario_id]["timerange"],
                    "cost_profile_id": profile_id,
                    "frequency": frequency["frequency"],
                    "phase_offset_months": frequency["phase_offset_months"],
                    "strategy_id": strategy_id,
                    "source": str(source),
                }
            )
        if result_ids != set(frequency_definitions):
            raise ValueError(f"scenario does not contain the complete frequency matrix: {source}")

    missing = sorted(set(expected) - observed)
    frequency_coverage = [
        {
            "frequency": frequency,
            "expected_phase_offsets": list(EXPECTED_PHASES[frequency]),
            "observed_phase_offsets": sorted(
                observed_phases.get(frequency, set()),
                key=lambda value: -1 if value is None else value,
            ),
        }
        for frequency in FREQUENCY_ORDER
    ]
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"],
        "policy_id": policy["policy_id"],
        "repository_commit": repository_commit,
        "phase": phase,
        "planned_scenario_count": len(expected),
        "completed_scenario_count": len(observed),
        "missing_scenario_ids": missing,
        "profile_coverage": [
            {"cost_profile_id": profile_id, **profile_counts[profile_id]}
            for profile_id in campaign["cost_profiles"]
        ],
        "frequency_coverage": frequency_coverage,
        "inventory": sorted(
            inventory,
            key=lambda row: (
                row["cost_profile_id"],
                row["window_set_id"],
                row["timerange"],
                FREQUENCY_ORDER.index(row["frequency"]),
                -1
                if row["phase_offset_months"] is None
                else row["phase_offset_months"],
            ),
        ),
        "disclosures": campaign["disclosures"],
        "ranking_status": "not_performed_issue_116",
    }


def write_coverage_outputs(payload: Mapping[str, Any], output_dir: Path) -> None:
    """Write compact aggregate coverage artifacts and a GitHub job summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "passive-frequency-coverage.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = payload["inventory"]
    with (output_dir / "passive-frequency-inventory.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        fields = list(rows[0]) if rows else ["scenario_id"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Passive DCA frequency campaign",
        "",
        f"Phase: **{payload['phase']}**",
        "",
        (
            f"Completed scenarios: **{payload['completed_scenario_count']} / "
            f"{payload['planned_scenario_count']}**"
        ),
        "",
        "| Cost profile | Scenarios | Frequency results |",
        "| --- | ---: | ---: |",
    ]
    for row in payload["profile_coverage"]:
        lines.append(
            f"| {row['cost_profile_id']} | {row['scenarios']} | "
            f"{row['frequency_results']} |"
        )
    lines.extend(
        [
            "",
            "| Frequency | Observed nuisance phases |",
            "| --- | --- |",
        ]
    )
    for row in payload["frequency_coverage"]:
        phases = ", ".join(
            "predefined weekday" if value is None else str(value)
            for value in row["observed_phase_offsets"]
        )
        lines.append(f"| {row['frequency']} | {phases or 'none'} |")
    if payload["missing_scenario_ids"]:
        lines.extend(
            [
                "",
                f"Missing scenarios: **{len(payload['missing_scenario_ids'])}**.",
            ]
        )
    lines.extend(
        [
            "",
            "No frequency or calendar phase is ranked in this workflow.",
            "Issue #116 performs phase-aggregated analysis and must never select the best phase.",
            "",
        ]
    )
    report = "\n".join(lines)
    (output_dir / "job-summary.md").write_text(report, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--campaign", type=Path, required=True)
        command.add_argument("--registry", type=Path, required=True)
        command.add_argument("--policy", type=Path, required=True)
        command.add_argument(
            "--cost-profile-dir",
            type=Path,
            default=DEFAULT_COST_PROFILE_DIR,
        )

    validate = subparsers.add_parser("validate")
    common(validate)
    validate.add_argument("--output", type=Path, required=True)

    plan = subparsers.add_parser("plan")
    common(plan)
    plan.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    plan.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate")
    common(aggregate)
    aggregate.add_argument(
        "--phase",
        choices=("exploratory", "confirmation"),
        required=True,
    )
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    campaign = load_json(args.campaign)
    policy = load_json(args.policy)
    registry = load_registry(args.registry)
    validation = validate_campaign(
        campaign,
        registry,
        policy,
        cost_profile_dir=args.cost_profile_dir,
    )
    if args.command == "validate":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(validation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    if args.command == "plan":
        rows = [row for row in plan_campaign(campaign) if row["phase"] == args.phase]
        if not rows:
            raise ValueError(f"campaign has no scenarios for phase: {args.phase}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    result_files = sorted(args.results_dir.rglob("frequency-scenario.json"))
    payload = aggregate_coverage(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase=args.phase,
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    write_coverage_outputs(payload, args.output_dir)


if __name__ == "__main__":
    main()
