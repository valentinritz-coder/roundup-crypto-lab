"""Phase-safe orchestration for DCA robustness and confirmation campaigns."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from roundup_crypto_lab.dca_robustness_campaign import (
    SCHEMA_VERSION,
    SURVIVOR_VERSION,
    aggregate_campaign,
    load_json,
    materialize_registry,
    plan_campaign,
)


def campaign_variants(campaign: Mapping[str, Any], phase: str) -> list[dict[str, Any]]:
    if phase not in {"exploratory", "confirmation"}:
        raise ValueError("phase must be exploratory or confirmation")
    variants = [
        {
            "variant_id": "frozen-default",
            "strategy_id": None,
            "overrides": {},
            "role": "frozen-default",
        }
    ]
    if phase == "confirmation":
        return variants
    seen = {"frozen-default"}
    for family in campaign.get("parameter_neighborhoods", []):
        strategy_id = str(family["strategy_id"])
        for variant in family.get("variants", []):
            variant_id = str(variant["variant_id"])
            if variant_id == "frozen-default":
                continue
            if variant_id in seen:
                raise ValueError(f"duplicate parameter-neighborhood variant: {variant_id}")
            seen.add(variant_id)
            variants.append(
                {
                    "variant_id": variant_id,
                    "strategy_id": strategy_id,
                    "overrides": dict(variant.get("overrides", {})),
                    "role": "neighborhood-diagnostic",
                }
            )
    return variants


def phase_plan(campaign: Mapping[str, Any], phase: str) -> list[dict[str, Any]]:
    scenarios = [row for row in plan_campaign(campaign) if row["phase"] == phase]
    if not scenarios:
        raise ValueError(f"campaign has no scenarios for phase: {phase}")
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        for variant in campaign_variants(campaign, phase):
            rows.append({**scenario, **variant})
    return rows


def write_variant_registry(
    campaign: Mapping[str, Any],
    registry: Mapping[str, Any],
    variant_id: str,
    output: Path,
) -> None:
    variants = {row["variant_id"]: row for row in campaign_variants(campaign, "exploratory")}
    if variant_id not in variants:
        raise ValueError(f"unknown campaign variant: {variant_id}")
    variant = variants[variant_id]
    if variant_id == "frozen-default":
        payload = dict(registry)
    else:
        payload = materialize_registry(
            registry,
            str(variant["strategy_id"]),
            variant["overrides"],
        )
        payload["registry_id"] = f"{registry['registry_id']}::{variant_id}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_outputs(payload: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dca-robustness-campaign.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = payload["aggregate_statistics"]
    with (output_dir / "dca-robustness-campaign.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = list(rows[0]) if rows else ["pair", "phase", "variant_id", "strategy"]
        for row in rows[1:]:
            fields.extend(field for field in row if field not in fields)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "dca-robustness-survivors.json").write_text(
        json.dumps(payload["survivor_artifact"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# DCA robustness campaign",
        "",
        "> Overlapping rolling windows are not independent observations.",
        "",
        f"Phase executed: **{payload['phase']}**.",
        "",
        "Neighborhood variants are diagnostics only and cannot enter the survivor artifact.",
        "",
        "| Pair | Variant | Strategy | Windows | Value win rate | Quantity win rate | Classification |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['pair']} | {row['variant_id']} | {row['strategy']} | "
            f"{row['scenarios_evaluated']} | {row['final_value_win_rate']} | "
            f"{row['quantity_win_rate']} | {row['classification']} |"
        )
    report = "\n".join(lines) + "\n"
    (output_dir / "dca-robustness-report.md").write_text(report, encoding="utf-8")
    (output_dir / "job-summary.md").write_text(report, encoding="utf-8")


def aggregate_phase(
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
    phase: str,
    results_dir: Path,
    repository_commit: str,
) -> dict[str, Any]:
    statistics: list[dict[str, Any]] = []
    frozen_survivors: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    variants = campaign_variants(campaign, phase)
    for variant in variants:
        variant_id = variant["variant_id"]
        files = sorted((results_dir / variant_id).rglob("controlled-comparison.json"))
        if not files:
            diagnostics.append({"variant_id": variant_id, "reason": "no result files"})
            continue
        digests = {
            load_json(path).get("registry", {}).get("registry_digest") for path in files
        }
        if len(digests) != 1 or None in digests:
            raise ValueError(f"variant registry identities are incompatible: {variant_id}")
        payload = aggregate_campaign(
            campaign=campaign,
            policy=policy,
            result_files=files,
            registry_digest=str(next(iter(digests))),
            repository_commit=repository_commit,
        )
        target_strategy = variant.get("strategy_id")
        for row in payload["aggregate_statistics"]:
            if row["phase"] != phase:
                continue
            if variant_id != "frozen-default" and row["strategy"] not in {
                policy["control"],
                target_strategy,
            }:
                continue
            item = dict(row)
            item["variant_id"] = variant_id
            item["variant_role"] = variant["role"]
            if variant_id != "frozen-default":
                item["policy_classification"] = item["classification"]
                item["classification"] = "neighborhood diagnostic"
            statistics.append(item)
        if variant_id == "frozen-default" and phase == "exploratory":
            frozen_survivors.extend(payload["survivor_artifact"]["survivors"])
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"],
        "policy_id": policy["policy_id"],
        "repository_commit": repository_commit,
        "phase": phase,
        "disclosures": campaign["disclosures"],
        "planned_scenarios": phase_plan(campaign, phase),
        "aggregate_statistics": sorted(
            statistics,
            key=lambda row: (row["pair"], row["variant_id"], row["strategy"]),
        ),
        "parameter_neighborhoods": campaign.get("parameter_neighborhoods", []),
        "diagnostics": diagnostics,
        "survivor_artifact": {
            "schema_version": SURVIVOR_VERSION,
            "phase": phase,
            "source_variant": "frozen-default",
            "survivors": frozen_survivors if phase == "exploratory" else [],
        },
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--campaign", type=Path, required=True)
    plan.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    plan.add_argument("--output", type=Path, required=True)
    materialize = subparsers.add_parser("materialize-registry")
    materialize.add_argument("--campaign", type=Path, required=True)
    materialize.add_argument("--registry", type=Path, required=True)
    materialize.add_argument("--variant-id", required=True)
    materialize.add_argument("--output", type=Path, required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--campaign", type=Path, required=True)
    aggregate.add_argument("--policy", type=Path, required=True)
    aggregate.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    campaign = load_json(args.campaign)
    if args.command == "plan":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(phase_plan(campaign, args.phase), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    if args.command == "materialize-registry":
        write_variant_registry(
            campaign,
            load_json(args.registry),
            args.variant_id,
            args.output,
        )
        return
    payload = aggregate_phase(
        campaign=campaign,
        policy=load_json(args.policy),
        phase=args.phase,
        results_dir=args.results_dir,
        repository_commit=args.repository_commit,
    )
    _write_outputs(payload, args.output_dir)


if __name__ == "__main__":
    main()
