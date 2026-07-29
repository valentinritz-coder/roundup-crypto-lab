"""Finalize, execute and summarize the passive DCA frequency research protocol."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab import passive_dca_frequency_campaign as campaign_module
from roundup_crypto_lab.dca_registry import StrategyRegistry, load_registry
from roundup_crypto_lab.execution_costs import ExecutionCostProfile, resolve_cost_profile
from roundup_crypto_lab.passive_dca_frequency_analysis import (
    aggregate_frequency_results,
    write_frequency_analysis_outputs,
)
from roundup_crypto_lab.passive_dca_frequency_campaign import (
    FREQUENCY_ORDER,
    aggregate_coverage,
    load_json,
    plan_campaign,
    validate_campaign,
    write_coverage_outputs,
)

RESEARCH_SCHEMA_VERSION = "passive-dca-frequency-research/v1"
CONCLUSION_SCHEMA_VERSION = "passive-dca-frequency-research-conclusion/v1"
RESEARCH_PROFILE_IDS = (
    "frictionless-control-v1",
    "proportional-fee-v1",
    "proportional-plus-spread-v1",
    "hypothetical-fixed-cost-v1",
)
EXPECTED_RESEARCH_KEYS = {
    "schema_version",
    "research_id",
    "base_campaign_path",
    "registry_path",
    "policy_path",
    "cost_profile_dir",
    "cost_profiles",
    "primary_research_phase",
    "confirmation_phase",
    "realistic_profile_id",
    "fixed_cost_sensitivity_profile_id",
    "disclosures",
}


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    result = []
    for item in value:
        result.append(_identifier(item, f"{name}[]"))
    return result


def load_research_protocol(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    keys = set(payload)
    missing = sorted(EXPECTED_RESEARCH_KEYS - keys)
    extra = sorted(keys - EXPECTED_RESEARCH_KEYS)
    if missing:
        raise ValueError(f"research protocol is missing keys: {', '.join(missing)}")
    if extra:
        raise ValueError(f"research protocol has unsupported keys: {', '.join(extra)}")
    if payload.get("schema_version") != RESEARCH_SCHEMA_VERSION:
        raise ValueError(f"research schema must be {RESEARCH_SCHEMA_VERSION}")
    _identifier(payload.get("research_id"), "research_id")
    for name in (
        "base_campaign_path",
        "registry_path",
        "policy_path",
        "cost_profile_dir",
        "realistic_profile_id",
        "fixed_cost_sensitivity_profile_id",
    ):
        _identifier(payload.get(name), name)
    profiles = tuple(_string_list(payload.get("cost_profiles"), "cost_profiles"))
    if profiles != RESEARCH_PROFILE_IDS:
        raise ValueError(
            "research profiles must be frictionless, proportional fee, "
            "proportional plus spread, then hypothetical fixed cost"
        )
    if payload.get("primary_research_phase") != "exploratory":
        raise ValueError("primary_research_phase must remain exploratory")
    if payload.get("confirmation_phase") != "confirmation":
        raise ValueError("confirmation_phase must remain confirmation")
    if payload.get("realistic_profile_id") != "proportional-plus-spread-v1":
        raise ValueError("realistic_profile_id must remain proportional-plus-spread-v1")
    if payload.get("fixed_cost_sensitivity_profile_id") != "hypothetical-fixed-cost-v1":
        raise ValueError(
            "fixed_cost_sensitivity_profile_id must remain hypothetical-fixed-cost-v1"
        )
    _string_list(payload.get("disclosures"), "disclosures")
    return payload


def materialize_research_campaign(
    research: Mapping[str, Any],
    *,
    repository_root: Path = Path("."),
) -> dict[str, Any]:
    base_path = repository_root / str(research["base_campaign_path"])
    base = load_json(base_path)
    result = deepcopy(base)
    result["campaign_id"] = str(research["research_id"])
    result["cost_profiles"] = list(RESEARCH_PROFILE_IDS)
    disclosures = list(base.get("disclosures", []))
    disclosures.extend(str(item) for item in research["disclosures"])
    result["disclosures"] = disclosures
    return result


@contextmanager
def _research_profile_contract() -> Iterator[None]:
    previous = campaign_module.REQUIRED_PROFILE_IDS
    campaign_module.REQUIRED_PROFILE_IDS = RESEARCH_PROFILE_IDS
    try:
        yield
    finally:
        campaign_module.REQUIRED_PROFILE_IDS = previous


def _validate_profile_roles(
    research: Mapping[str, Any],
    *,
    cost_profile_dir: Path,
) -> list[ExecutionCostProfile]:
    profiles = [
        resolve_cost_profile(profile_id, search_dir=cost_profile_dir)
        for profile_id in RESEARCH_PROFILE_IDS
    ]
    fixed = next(
        profile
        for profile in profiles
        if profile.cost_profile_id == research["fixed_cost_sensitivity_profile_id"]
    )
    realistic = next(
        profile
        for profile in profiles
        if profile.cost_profile_id == research["realistic_profile_id"]
    )
    if fixed.profile_kind != "sensitivity" or fixed.fixed_order_fee <= 0:
        raise ValueError("fixed-cost profile must be an explicit positive sensitivity")
    if realistic.fixed_order_fee != 0 or realistic.half_spread_ratio <= 0:
        raise ValueError("realistic profile must contain spread but no fixed order fee")
    if "hypothetical" not in fixed.description.lower():
        raise ValueError("fixed-cost sensitivity description must say it is hypothetical")
    return profiles


def validate_research_protocol(
    research: Mapping[str, Any],
    campaign: Mapping[str, Any],
    registry: StrategyRegistry,
    policy: Mapping[str, Any],
    *,
    cost_profile_dir: Path,
) -> dict[str, Any]:
    profiles = _validate_profile_roles(research, cost_profile_dir=cost_profile_dir)
    with _research_profile_contract():
        campaign_validation = validate_campaign(
            campaign,
            registry,
            policy,
            cost_profile_dir=cost_profile_dir,
        )
    scenario_counts = campaign_validation["scenario_counts"]
    if scenario_counts != {"exploratory": 60, "confirmation": 4}:
        raise ValueError(f"unexpected research scenario counts: {scenario_counts}")
    return {
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "research_id": research["research_id"],
        "base_campaign_path": research["base_campaign_path"],
        "campaign_validation": campaign_validation,
        "profile_roles": {
            "frictionless_control": profiles[0].cost_profile_id,
            "proportional_fee": profiles[1].cost_profile_id,
            "realistic": realistic_profile_id(research),
            "fixed_cost_sensitivity": fixed_profile_id(research),
        },
        "scenario_counts": scenario_counts,
        "strategy_result_counts": {
            "exploratory": scenario_counts["exploratory"] * 7,
            "confirmation": scenario_counts["confirmation"] * 7,
        },
        "disclosures": list(research["disclosures"]),
    }


def realistic_profile_id(research: Mapping[str, Any]) -> str:
    return str(research["realistic_profile_id"])


def fixed_profile_id(research: Mapping[str, Any]) -> str:
    return str(research["fixed_cost_sensitivity_profile_id"])


def plan_research_campaign(
    campaign: Mapping[str, Any],
    *,
    phase: str,
) -> list[dict[str, Any]]:
    if phase not in {"exploratory", "confirmation"}:
        raise ValueError("phase must be exploratory or confirmation")
    rows = [row for row in plan_campaign(campaign) if row["phase"] == phase]
    if not rows:
        raise ValueError(f"research campaign has no scenarios for phase: {phase}")
    return rows


def _profile_findings(
    analysis: Mapping[str, Any],
    research: Mapping[str, Any],
) -> list[dict[str, Any]]:
    winners: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in analysis["rankings"]:
        if int(row["robust_rank"]) == 1:
            winners[str(row["cost_profile_id"])].append(row)

    findings = []
    for profile_id in RESEARCH_PROFILE_IDS:
        rows = sorted(winners.get(profile_id, []), key=lambda row: str(row["window_set_id"]))
        frequencies = [str(row["frequency"]) for row in rows]
        counts = Counter(frequencies)
        consensus = frequencies[0] if frequencies and len(counts) == 1 else None
        findings.append(
            {
                "cost_profile_id": profile_id,
                "profile_role": (
                    "fixed-cost sensitivity"
                    if profile_id == fixed_profile_id(research)
                    else "realistic"
                    if profile_id == realistic_profile_id(research)
                    else "control"
                    if profile_id == "frictionless-control-v1"
                    else "fee-only baseline"
                ),
                "window_set_winners": [
                    {
                        "window_set_id": row["window_set_id"],
                        "frequency": row["frequency"],
                        "median_net_terminal_value": row["median_net_terminal_value"],
                        "classification": row["classification"],
                    }
                    for row in rows
                ],
                "winner_counts": {
                    frequency: counts.get(frequency, 0) for frequency in FREQUENCY_ORDER
                },
                "consensus_frequency": consensus,
                "consensus_status": "unanimous" if consensus is not None else "mixed",
            }
        )
    return findings


def build_research_conclusion(
    analysis: Mapping[str, Any],
    research: Mapping[str, Any],
) -> dict[str, Any]:
    findings = _profile_findings(analysis, research)
    by_profile = {row["cost_profile_id"]: row for row in findings}
    realistic = by_profile[realistic_profile_id(research)]["consensus_frequency"]
    fixed = by_profile[fixed_profile_id(research)]["consensus_frequency"]
    changed: bool | None = None if realistic is None or fixed is None else realistic != fixed
    phase = str(analysis["research_phase"])
    return {
        "schema_version": CONCLUSION_SCHEMA_VERSION,
        "research_id": research["research_id"],
        "research_phase": phase,
        "interpretation_status": (
            "exploratory_ready_for_review"
            if phase == "exploratory"
            else "confirmation_holdout_do_not_reinterpret_exploratory"
        ),
        "profile_findings": findings,
        "sensitivity_comparison": {
            "realistic_profile_id": realistic_profile_id(research),
            "realistic_consensus_frequency": realistic,
            "fixed_cost_profile_id": fixed_profile_id(research),
            "fixed_cost_consensus_frequency": fixed,
            "preferred_frequency_changed": changed,
        },
        "decision_rule": (
            "A consensus frequency exists only when every committed window set "
            "selects the same phase-aggregated rank-one frequency."
        ),
        "requires_human_review": True,
        "disclosures": list(research["disclosures"]),
    }


def write_research_conclusion(payload: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "passive-frequency-research-conclusion.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Passive DCA frequency research conclusion",
        "",
        f"Research phase: **{payload['research_phase']}**",
        f"Interpretation status: **{payload['interpretation_status']}**",
        "",
        "| Cost profile | Role | Consensus | Window-set winners |",
        "| --- | --- | --- | --- |",
    ]
    for row in payload["profile_findings"]:
        winners = ", ".join(
            f"{item['window_set_id']}: {item['frequency']}"
            for item in row["window_set_winners"]
        )
        lines.append(
            f"| {row['cost_profile_id']} | {row['profile_role']} | "
            f"{row['consensus_frequency'] or 'mixed'} | {winners or 'none'} |"
        )
    sensitivity = payload["sensitivity_comparison"]
    lines.extend(
        [
            "",
            "## Fixed-cost sensitivity",
            "",
            f"Realistic consensus: **{sensitivity['realistic_consensus_frequency'] or 'mixed'}**",
            f"Fixed-cost consensus: **{sensitivity['fixed_cost_consensus_frequency'] or 'mixed'}**",
            f"Preferred frequency changed: **{sensitivity['preferred_frequency_changed']}**",
            "",
            "No best calendar phase is selected. Mixed window-set evidence remains mixed.",
            "",
        ]
    )
    (output_dir / "research-summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _load_inputs(
    research_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], StrategyRegistry, dict[str, Any], Path]:
    research = load_research_protocol(research_path)
    campaign = materialize_research_campaign(research)
    registry = load_registry(Path(str(research["registry_path"])))
    policy = load_json(Path(str(research["policy_path"])))
    cost_profile_dir = Path(str(research["cost_profile_dir"]))
    return research, campaign, registry, policy, cost_profile_dir


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--research", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--research", type=Path, required=True)
    plan.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    plan.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--research", type=Path, required=True)
    aggregate.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    research, campaign, registry, policy, cost_profile_dir = _load_inputs(args.research)
    validation = validate_research_protocol(
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
        rows = plan_research_campaign(campaign, phase=args.phase)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return

    result_files = sorted(args.results_dir.rglob("frequency-scenario.json"))
    if not result_files:
        raise ValueError("passive frequency research found no scenario artifacts")
    coverage = aggregate_coverage(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase=args.phase,
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    if coverage["missing_scenario_ids"]:
        raise ValueError(
            f"research campaign is incomplete: {len(coverage['missing_scenario_ids'])} missing"
        )
    write_coverage_outputs(coverage, args.output_dir / "coverage")
    analysis = aggregate_frequency_results(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase=args.phase,
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    write_frequency_analysis_outputs(analysis, args.output_dir / "analysis")
    conclusion = build_research_conclusion(analysis, research)
    write_research_conclusion(conclusion, args.output_dir / "conclusion")
    (args.output_dir / "resolved-research-campaign.json").write_text(
        json.dumps(campaign, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
