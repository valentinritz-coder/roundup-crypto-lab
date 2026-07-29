"""Run the passive DCA frequency research with a frozen data-quality amendment."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from roundup_crypto_lab import passive_dca_frequency_research as base_research
from roundup_crypto_lab.dca_registry import StrategyRegistry, load_registry
from roundup_crypto_lab.passive_dca_frequency_analysis import (
    aggregate_frequency_results,
    write_frequency_analysis_outputs,
)
from roundup_crypto_lab.passive_dca_frequency_campaign import (
    aggregate_coverage,
    load_json,
    plan_campaign,
    validate_campaign,
    write_coverage_outputs,
)

AMENDMENT_SCHEMA_VERSION = "passive-dca-frequency-data-quality-amendment/v1"
EXPECTED_AMENDMENT_KEYS = {
    "schema_version",
    "amendment_id",
    "research_id",
    "source_run_id",
    "observed_gap",
    "window_start_overrides",
    "excluded_windows",
    "expected_scenario_counts",
    "rationale",
    "disclosure",
}
EXPECTED_WINDOW_START_OVERRIDES = {
    "rolling-24m-6m-step": "20180701",
    "non-overlapping-24m": "20200101",
    "rolling-48m-12m-step": "20190101",
}
EXPECTED_EXCLUDED_WINDOWS = {
    ("rolling-24m-6m-step", "20180101-20200101"),
    ("non-overlapping-24m", "20180101-20200101"),
    ("rolling-48m-12m-step", "20180101-20220101"),
}
EXPECTED_SCENARIO_COUNTS = {"exploratory": 48, "confirmation": 4}


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _day(value: object, name: str) -> str:
    text = _identifier(value, name)
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYYMMDD") from exc
    return text


def load_data_quality_amendment(path: Path) -> dict[str, Any]:
    """Load and strictly validate the committed research amendment."""
    payload = load_json(path)
    missing = sorted(EXPECTED_AMENDMENT_KEYS - set(payload))
    extra = sorted(set(payload) - EXPECTED_AMENDMENT_KEYS)
    if missing:
        raise ValueError(f"data-quality amendment is missing keys: {', '.join(missing)}")
    if extra:
        raise ValueError(
            f"data-quality amendment has unsupported keys: {', '.join(extra)}"
        )
    if payload.get("schema_version") != AMENDMENT_SCHEMA_VERSION:
        raise ValueError(f"amendment schema must be {AMENDMENT_SCHEMA_VERSION}")
    _identifier(payload.get("amendment_id"), "amendment_id")
    _identifier(payload.get("research_id"), "research_id")
    source_run_id = payload.get("source_run_id")
    if isinstance(source_run_id, bool) or not isinstance(source_run_id, int):
        raise ValueError("source_run_id must be an integer")
    if source_run_id != 30450539478:
        raise ValueError("source_run_id must remain the observed exploratory run")

    gap = payload.get("observed_gap")
    if not isinstance(gap, Mapping):
        raise ValueError("observed_gap must be an object")
    expected_gap = {
        "pair": "BTC/EUR",
        "timeframe": "4h",
        "start": "2018-01-11T20:00:00+00:00",
        "end": "2018-01-13T08:00:00+00:00",
        "duration_hours": 36,
    }
    if dict(gap) != expected_gap:
        raise ValueError("observed_gap must match the recorded Kraken gap exactly")

    overrides = payload.get("window_start_overrides")
    if not isinstance(overrides, Mapping):
        raise ValueError("window_start_overrides must be an object")
    normalized_overrides = {
        _identifier(key, "window_start_overrides key"): _day(
            value, f"window_start_overrides.{key}"
        )
        for key, value in overrides.items()
    }
    if normalized_overrides != EXPECTED_WINDOW_START_OVERRIDES:
        raise ValueError("window_start_overrides must match the frozen amendment")

    excluded = payload.get("excluded_windows")
    if not isinstance(excluded, list) or not excluded:
        raise ValueError("excluded_windows must be a non-empty array")
    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(excluded):
        if not isinstance(row, Mapping) or set(row) != {"window_set_id", "timerange"}:
            raise ValueError(f"excluded_windows[{index}] must contain window_set_id and timerange")
        window_set_id = _identifier(row.get("window_set_id"), "window_set_id")
        timerange = _identifier(row.get("timerange"), "timerange")
        if len(timerange) != 17 or timerange[8] != "-":
            raise ValueError("excluded window timerange must use YYYYMMDD-YYYYMMDD")
        _day(timerange[:8], "timerange start")
        _day(timerange[9:], "timerange end")
        identities.add((window_set_id, timerange))
    if identities != EXPECTED_EXCLUDED_WINDOWS:
        raise ValueError("excluded_windows must match the three invalid market windows")

    counts = payload.get("expected_scenario_counts")
    if counts != EXPECTED_SCENARIO_COUNTS:
        raise ValueError("expected_scenario_counts must remain exploratory=48, confirmation=4")
    _identifier(payload.get("rationale"), "rationale")
    _identifier(payload.get("disclosure"), "disclosure")
    return payload


def apply_data_quality_amendment(
    research: Mapping[str, Any],
    campaign: Mapping[str, Any],
    amendment: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a research campaign with only the invalid 2018 windows removed."""
    if amendment.get("research_id") != research.get("research_id"):
        raise ValueError("amendment research_id does not match the research protocol")
    result = deepcopy(dict(campaign))
    windows = result.get("window_sets")
    if not isinstance(windows, list):
        raise ValueError("research campaign window_sets must be an array")

    overrides = dict(amendment["window_start_overrides"])
    seen: set[str] = set()
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError("research campaign window sets must be objects")
        window_id = str(window.get("window_set_id"))
        if window_id not in overrides:
            continue
        if window.get("phase") != "exploratory":
            raise ValueError("data-quality amendment may change only exploratory windows")
        if window.get("start") != "20180101":
            raise ValueError(f"unexpected preregistered start for {window_id}")
        window["start"] = overrides[window_id]
        seen.add(window_id)
    if seen != set(overrides):
        missing = sorted(set(overrides) - seen)
        raise ValueError(f"amendment window sets were not found: {', '.join(missing)}")

    disclosures = list(result.get("disclosures", []))
    disclosure = str(amendment["disclosure"])
    if disclosure not in disclosures:
        disclosures.append(disclosure)
    result["disclosures"] = disclosures
    return result


def _expected_removed_scenario_ids(
    research: Mapping[str, Any],
    amendment: Mapping[str, Any],
) -> set[str]:
    profiles = tuple(str(value) for value in research["cost_profiles"])
    return {
        f"exploratory::{profile_id}::{window_set_id}::BTC_EUR::{timerange}"
        for profile_id in profiles
        for window_set_id, timerange in EXPECTED_EXCLUDED_WINDOWS
    }


def validate_amended_research_protocol(
    research: Mapping[str, Any],
    base_campaign: Mapping[str, Any],
    amended_campaign: Mapping[str, Any],
    amendment: Mapping[str, Any],
    registry: StrategyRegistry,
    policy: Mapping[str, Any],
    *,
    cost_profile_dir: Path,
) -> dict[str, Any]:
    """Validate the original protocol, the amendment and the resulting matrix."""
    baseline = base_research.validate_research_protocol(
        research,
        base_campaign,
        registry,
        policy,
        cost_profile_dir=cost_profile_dir,
    )
    with base_research._research_profile_contract():
        amended_validation = validate_campaign(
            amended_campaign,
            registry,
            policy,
            cost_profile_dir=cost_profile_dir,
        )
    counts = amended_validation["scenario_counts"]
    if counts != EXPECTED_SCENARIO_COUNTS:
        raise ValueError(f"unexpected amended scenario counts: {counts}")

    base_rows = plan_campaign(base_campaign)
    amended_rows = plan_campaign(amended_campaign)
    base_ids = {str(row["scenario_id"]) for row in base_rows}
    amended_ids = {str(row["scenario_id"]) for row in amended_rows}
    removed = base_ids - amended_ids
    added = amended_ids - base_ids
    expected_removed = _expected_removed_scenario_ids(research, amendment)
    if removed != expected_removed:
        raise ValueError("amendment removed scenarios other than the frozen invalid windows")
    if added:
        raise ValueError("amendment must not add replacement market windows")
    if any(not scenario_id.startswith("exploratory::") for scenario_id in removed):
        raise ValueError("amendment must not alter confirmation scenarios")

    return {
        "schema_version": AMENDMENT_SCHEMA_VERSION,
        "research_id": research["research_id"],
        "amendment_id": amendment["amendment_id"],
        "source_run_id": amendment["source_run_id"],
        "base_protocol_validation": baseline,
        "amended_campaign_validation": amended_validation,
        "scenario_counts": counts,
        "strategy_result_counts": {
            "exploratory": counts["exploratory"] * 7,
            "confirmation": counts["confirmation"] * 7,
        },
        "removed_scenario_ids": sorted(removed),
        "observed_gap": dict(amendment["observed_gap"]),
        "rationale": amendment["rationale"],
        "disclosure": amendment["disclosure"],
    }


def _load_inputs(
    research_path: Path,
    amendment_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    StrategyRegistry,
    dict[str, Any],
    Path,
]:
    research = base_research.load_research_protocol(research_path)
    amendment = load_data_quality_amendment(amendment_path)
    base_campaign = base_research.materialize_research_campaign(research)
    amended_campaign = apply_data_quality_amendment(research, base_campaign, amendment)
    registry = load_registry(Path(str(research["registry_path"])))
    policy = load_json(Path(str(research["policy_path"])))
    cost_profile_dir = Path(str(research["cost_profile_dir"]))
    return (
        research,
        amendment,
        base_campaign,
        amended_campaign,
        registry,
        policy,
        cost_profile_dir,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--amendment", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--output", type=Path, required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    plan.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--phase", choices=("exploratory", "confirmation"), required=True)
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    (
        research,
        amendment,
        base_campaign,
        campaign,
        registry,
        policy,
        cost_profile_dir,
    ) = _load_inputs(args.research, args.amendment)
    validation = validate_amended_research_protocol(
        research,
        base_campaign,
        campaign,
        amendment,
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
        rows = base_research.plan_research_campaign(campaign, phase=args.phase)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return

    result_files = sorted(args.results_dir.rglob("frequency-scenario.json"))
    if not result_files:
        raise ValueError("amended passive frequency research found no scenario artifacts")
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
            f"amended research campaign is incomplete: "
            f"{len(coverage['missing_scenario_ids'])} missing"
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
    conclusion = base_research.build_research_conclusion(analysis, research)
    base_research.write_research_conclusion(conclusion, args.output_dir / "conclusion")
    (args.output_dir / "resolved-research-campaign.json").write_text(
        json.dumps(campaign, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "resolved-data-quality-amendment.json").write_text(
        json.dumps(amendment, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
