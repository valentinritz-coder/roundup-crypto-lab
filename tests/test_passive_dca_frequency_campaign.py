from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.passive_dca_frequency_campaign import (
    EXPECTED_PHASES,
    aggregate_coverage,
    load_json,
    plan_campaign,
    validate_campaign,
    validate_frequency_registry,
)

CAMPAIGN_PATH = Path("config/passive-dca-frequency-campaign.json")
REGISTRY_PATH = Path("config/passive-dca-frequency-strategies.json")
POLICY_PATH = Path("config/passive-dca-frequency-policy.json")
PROFILE_DIR = Path("config/execution-cost-profiles")


def _inputs():
    campaign = load_json(CAMPAIGN_PATH)
    registry = load_registry(REGISTRY_PATH)
    policy = load_json(POLICY_PATH)
    return campaign, registry, policy


def test_committed_frequency_campaign_inputs_validate() -> None:
    campaign, registry, policy = _inputs()
    summary = validate_campaign(
        campaign,
        registry,
        policy,
        cost_profile_dir=PROFILE_DIR,
    )
    assert summary["scenario_counts"] == {
        "exploratory": 45,
        "confirmation": 3,
    }
    assert [row["strategy_id"] for row in summary["frequencies"]] == [
        "weekly-dca",
        "monthly-dca",
        "every-two-months-phase-0",
        "every-two-months-phase-1",
        "quarterly-phase-0",
        "quarterly-phase-1",
        "quarterly-phase-2",
    ]
    assert [row["cost_profile_id"] for row in summary["cost_profiles"]] == [
        "frictionless-control-v1",
        "proportional-fee-v1",
        "proportional-plus-spread-v1",
    ]


def test_campaign_plan_is_deterministic_and_phase_safe() -> None:
    campaign, _, _ = _inputs()
    first = plan_campaign(campaign)
    second = plan_campaign(campaign)
    assert first == second
    assert len(first) == 48
    assert len({row["scenario_id"] for row in first}) == len(first)
    assert all(row["campaign_id"] == campaign["campaign_id"] for row in first)

    exploratory = [row for row in first if row["phase"] == "exploratory"]
    confirmation = [row for row in first if row["phase"] == "confirmation"]
    assert max(row["timerange"][9:] for row in exploratory) <= min(
        row["timerange"][:8] for row in confirmation
    )
    assert {row["pair"] for row in first} == {"BTC/EUR"}


def test_dedicated_registry_contains_every_phase_once() -> None:
    _, registry, _ = _inputs()
    metadata = validate_frequency_registry(registry)
    actual = {row["frequency"]: set() for row in metadata}
    for row in metadata:
        actual[row["frequency"]].add(row["phase_offset_months"])
    assert actual == {
        frequency: set(phases)
        for frequency, phases in EXPECTED_PHASES.items()
    }


def test_general_registry_is_rejected_for_frequency_campaign() -> None:
    registry = load_registry(Path("config/dca-strategy-registry.json"))
    with pytest.raises(ValueError, match="non-passive strategies"):
        validate_frequency_registry(registry)


def test_coverage_index_preserves_phases_without_ranking(
    tmp_path: Path,
) -> None:
    campaign, registry, policy = _inputs()
    scenario = next(
        row
        for row in plan_campaign(campaign)
        if row["phase"] == "confirmation"
        and row["cost_profile_id"] == "frictionless-control-v1"
    )
    definitions = validate_frequency_registry(registry)
    result_path = tmp_path / "frequency-scenario.json"
    result_path.write_text(
        json.dumps(
            {
                "campaign": {
                    "scenario_id": scenario["scenario_id"],
                    "phase": "confirmation",
                },
                "registry": {"registry_digest": registry.digest},
                "execution_cost_profile": {
                    "cost_profile_id": scenario["cost_profile_id"]
                },
                "results": [
                    {"strategy": {"strategy_id": row["strategy_id"]}}
                    for row in definitions
                ],
            }
        ),
        encoding="utf-8",
    )
    payload = aggregate_coverage(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase="confirmation",
        result_files=[result_path],
        repository_commit="a" * 40,
    )
    assert payload["completed_scenario_count"] == 1
    assert payload["planned_scenario_count"] == 3
    assert payload["ranking_status"] == "not_performed_issue_116"
    assert "ranking" not in payload
    quarterly = next(
        row
        for row in payload["frequency_coverage"]
        if row["frequency"] == "quarterly"
    )
    assert quarterly["observed_phase_offsets"] == [0, 1, 2]
