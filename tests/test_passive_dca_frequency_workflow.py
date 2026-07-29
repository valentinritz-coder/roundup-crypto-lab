from pathlib import Path

import yaml

WORKFLOW_PATH = Path(".github/workflows/passive-dca-frequency-campaign.yml")


def test_passive_frequency_workflow_is_dispatchable_and_sharded() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert dispatch["phase"]["options"] == ["exploratory", "confirmation"]
    assert set(workflow["jobs"]) == {"prepare", "campaign", "aggregate"}
    assert "fromJSON(needs.prepare.outputs.shards)" in text
    assert "actions/cache/restore@v4" in text
    assert "actions/upload-artifact@v4" in text


def test_workflow_validates_and_runs_only_passive_frequency_inputs() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "config/passive-dca-frequency-strategies.json" in text
    assert "config/passive-dca-frequency-policy.json" in text
    assert "passive_dca_frequency_campaign validate" in text
    assert "passive_dca_frequency_scenario" in text
    assert "dca_campaign_scenario" not in text
    assert "dca_controlled_comparison" not in text
    assert "pilot" not in text.lower()


def test_workflow_paths_encode_cost_profile_and_research_identity() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert (
        "artifacts/results/${scenario_phase}/${cost_profile}/${safe_pair}/"
        "${window_set}/${timerange}"
    ) in text
    assert "--cost-profile \"$cost_profile\"" in text
    assert "--scenario-id \"$scenario_id\"" in text
    assert "passive_dca_frequency_campaign aggregate" in text
    assert "No frequency or calendar phase is ranked" not in text
