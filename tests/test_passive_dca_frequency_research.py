from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.passive_dca_frequency_campaign import load_json
from roundup_crypto_lab.passive_dca_frequency_research import (
    RESEARCH_PROFILE_IDS,
    build_research_conclusion,
    load_research_protocol,
    materialize_research_campaign,
    plan_research_campaign,
    validate_research_protocol,
)

RESEARCH_PATH = Path("config/passive-dca-frequency-research.json")
WORKFLOW_PATH = Path(".github/workflows/passive-dca-frequency-research.yml")


def _inputs():
    research = load_research_protocol(RESEARCH_PATH)
    campaign = materialize_research_campaign(research)
    registry = load_registry(Path(research["registry_path"]))
    policy = load_json(Path(research["policy_path"]))
    return research, campaign, registry, policy


def test_committed_research_protocol_materializes_four_profiles() -> None:
    research, campaign, registry, policy = _inputs()
    validation = validate_research_protocol(
        research,
        campaign,
        registry,
        policy,
        cost_profile_dir=Path(research["cost_profile_dir"]),
    )
    assert tuple(campaign["cost_profiles"]) == RESEARCH_PROFILE_IDS
    assert validation["scenario_counts"] == {
        "exploratory": 60,
        "confirmation": 4,
    }
    assert validation["strategy_result_counts"] == {
        "exploratory": 420,
        "confirmation": 28,
    }
    assert validation["profile_roles"] == {
        "frictionless_control": "frictionless-control-v1",
        "proportional_fee": "proportional-fee-v1",
        "realistic": "proportional-plus-spread-v1",
        "fixed_cost_sensitivity": "hypothetical-fixed-cost-v1",
    }


def test_research_overlay_does_not_mutate_base_campaign() -> None:
    research = load_research_protocol(RESEARCH_PATH)
    base_path = Path(research["base_campaign_path"])
    before = load_json(base_path)
    snapshot = deepcopy(before)
    materialized = materialize_research_campaign(research)
    assert before == snapshot
    assert before["cost_profiles"] == [
        "frictionless-control-v1",
        "proportional-fee-v1",
        "proportional-plus-spread-v1",
    ]
    assert materialized["cost_profiles"][-1] == "hypothetical-fixed-cost-v1"
    assert materialized["campaign_id"] == research["research_id"]


def test_final_research_plan_is_deterministic_and_phase_safe() -> None:
    _, campaign, _, _ = _inputs()
    first = plan_research_campaign(campaign, phase="exploratory")
    second = plan_research_campaign(campaign, phase="exploratory")
    confirmation = plan_research_campaign(campaign, phase="confirmation")
    assert first == second
    assert len(first) == 60
    assert len(confirmation) == 4
    assert len({row["scenario_id"] for row in first + confirmation}) == 64
    assert {row["cost_profile_id"] for row in first} == set(RESEARCH_PROFILE_IDS)
    assert max(row["timerange"][9:] for row in first) <= min(
        row["timerange"][:8] for row in confirmation
    )


def _winner(
    profile: str,
    window_set: str,
    frequency: str,
    *,
    rank: int = 1,
) -> dict[str, object]:
    return {
        "cost_profile_id": profile,
        "window_set_id": window_set,
        "frequency": frequency,
        "robust_rank": rank,
        "median_net_terminal_value": "100",
        "classification": "primary control" if frequency == "monthly" else "promising",
    }


def test_research_conclusion_reports_only_window_set_consensus() -> None:
    research = load_research_protocol(RESEARCH_PATH)
    windows = (
        "rolling-24m-6m-step",
        "non-overlapping-24m",
        "rolling-48m-12m-step",
    )
    rankings = []
    for profile in RESEARCH_PROFILE_IDS:
        winner = "quarterly" if profile == "hypothetical-fixed-cost-v1" else "monthly"
        for window in windows:
            rankings.append(_winner(profile, window, winner))
    conclusion = build_research_conclusion(
        {"research_phase": "exploratory", "rankings": rankings},
        research,
    )
    findings = {row["cost_profile_id"]: row for row in conclusion["profile_findings"]}
    assert findings["proportional-plus-spread-v1"]["consensus_frequency"] == "monthly"
    assert findings["hypothetical-fixed-cost-v1"]["consensus_frequency"] == "quarterly"
    assert conclusion["sensitivity_comparison"]["preferred_frequency_changed"] is True
    assert conclusion["requires_human_review"] is True


def test_research_conclusion_keeps_disagreement_mixed() -> None:
    research = load_research_protocol(RESEARCH_PATH)
    rankings = [
        _winner("frictionless-control-v1", "window-a", "monthly"),
        _winner("frictionless-control-v1", "window-b", "weekly"),
    ]
    conclusion = build_research_conclusion(
        {"research_phase": "exploratory", "rankings": rankings},
        research,
    )
    finding = next(
        row
        for row in conclusion["profile_findings"]
        if row["cost_profile_id"] == "frictionless-control-v1"
    )
    assert finding["consensus_frequency"] is None
    assert finding["consensus_status"] == "mixed"


def test_final_research_workflow_is_dispatchable_and_sharded() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert dispatch["phase"]["options"] == ["exploratory", "confirmation"]
    assert set(workflow["jobs"]) == {"prepare", "research", "aggregate"}
    assert "fromJSON(needs.prepare.outputs.shards)" in text
    assert "actions/cache/restore@v4" in text
    assert "passive_dca_frequency_research validate" in text
    assert "passive_dca_frequency_research aggregate" in text
    assert "passive_dca_frequency_scenario" in text
    assert "hypothetical-fixed-cost-v1" not in text
