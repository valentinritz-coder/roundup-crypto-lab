from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.passive_dca_frequency_campaign import load_json
from roundup_crypto_lab.passive_dca_frequency_research import (
    load_research_protocol,
    materialize_research_campaign,
    plan_research_campaign,
)
from roundup_crypto_lab.passive_dca_frequency_research_amended import (
    EXPECTED_EXCLUDED_WINDOWS,
    apply_data_quality_amendment,
    load_data_quality_amendment,
    validate_amended_research_protocol,
)

RESEARCH_PATH = Path("config/passive-dca-frequency-research.json")
AMENDMENT_PATH = Path(
    "config/passive-dca-frequency-research-data-quality-amendment.json"
)
REGISTRY_PATH = Path("config/passive-dca-frequency-strategies.json")
POLICY_PATH = Path("config/passive-dca-frequency-policy.json")
PROFILE_DIR = Path("config/execution-cost-profiles")
WORKFLOW_PATH = Path(
    ".github/workflows/passive-dca-frequency-research-amended.yml"
)


def _inputs():
    research = load_research_protocol(RESEARCH_PATH)
    amendment = load_data_quality_amendment(AMENDMENT_PATH)
    base_campaign = materialize_research_campaign(research)
    amended_campaign = apply_data_quality_amendment(
        research,
        base_campaign,
        amendment,
    )
    registry = load_registry(REGISTRY_PATH)
    policy = load_json(POLICY_PATH)
    return research, amendment, base_campaign, amended_campaign, registry, policy


def test_committed_data_quality_amendment_validates_exact_matrix() -> None:
    research, amendment, base_campaign, campaign, registry, policy = _inputs()
    validation = validate_amended_research_protocol(
        research,
        base_campaign,
        campaign,
        amendment,
        registry,
        policy,
        cost_profile_dir=PROFILE_DIR,
    )
    assert validation["scenario_counts"] == {
        "exploratory": 48,
        "confirmation": 4,
    }
    assert validation["strategy_result_counts"] == {
        "exploratory": 336,
        "confirmation": 28,
    }
    assert len(validation["removed_scenario_ids"]) == 12
    assert all(
        scenario_id.startswith("exploratory::")
        for scenario_id in validation["removed_scenario_ids"]
    )


def test_amendment_changes_only_exploratory_window_starts() -> None:
    research = load_research_protocol(RESEARCH_PATH)
    amendment = load_data_quality_amendment(AMENDMENT_PATH)
    base_campaign = materialize_research_campaign(research)
    original = deepcopy(base_campaign)
    amended = apply_data_quality_amendment(research, base_campaign, amendment)

    assert base_campaign == original
    starts = {
        row["window_set_id"]: row["start"]
        for row in amended["window_sets"]
        if row["phase"] == "exploratory"
    }
    assert starts == {
        "rolling-24m-6m-step": "20180701",
        "non-overlapping-24m": "20200101",
        "rolling-48m-12m-step": "20190101",
    }
    original_confirmation = [
        row for row in original["window_sets"] if row["phase"] == "confirmation"
    ]
    amended_confirmation = [
        row for row in amended["window_sets"] if row["phase"] == "confirmation"
    ]
    assert amended_confirmation == original_confirmation


def test_amended_plan_preserves_all_valid_windows_and_profiles() -> None:
    research, _, _, campaign, _, _ = _inputs()
    exploratory = plan_research_campaign(campaign, phase="exploratory")
    confirmation = plan_research_campaign(campaign, phase="confirmation")
    assert len(exploratory) == 48
    assert len(confirmation) == 4
    assert {row["cost_profile_id"] for row in exploratory} == set(
        research["cost_profiles"]
    )
    actual_windows = {
        (row["window_set_id"], row["timerange"])
        for row in exploratory
    }
    assert not (actual_windows & EXPECTED_EXCLUDED_WINDOWS)
    assert ("rolling-24m-6m-step", "20190701-20210701") in actual_windows
    assert ("rolling-24m-6m-step", "20210101-20230101") in actual_windows


def test_amendment_rejects_provenance_or_window_drift(tmp_path: Path) -> None:
    payload = load_json(AMENDMENT_PATH)
    payload["source_run_id"] = 1
    path = tmp_path / "bad-amendment.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source_run_id"):
        load_data_quality_amendment(path)

    payload = load_json(AMENDMENT_PATH)
    payload["window_start_overrides"]["rolling-24m-6m-step"] = "20190101"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="window_start_overrides"):
        load_data_quality_amendment(path)


def test_amended_workflow_runs_all_scenarios_before_failing_shard() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "Passive DCA frequency research amended" in workflow
    assert "passive_dca_frequency_research_amended" in workflow
    assert "passive-dca-frequency-research-data-quality-amendment.json" in workflow
    assert "expected = 48 if" in workflow
    assert "failed=0" in workflow
    assert "if ! python -m roundup_crypto_lab.passive_dca_frequency_scenario" in workflow
    assert "failed=1" in workflow
    assert 'exit "$failed"' in workflow
    assert "needs.research.result == 'success'" in workflow
