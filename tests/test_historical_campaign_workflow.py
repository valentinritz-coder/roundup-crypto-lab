from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / ".github/workflows/historical-crypto-campaign.yml"
COMPARISON = ROOT / ".github/workflows/all-strategy-comparison.yml"


def load_workflow(path: Path) -> dict[str, object]:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_historical_campaign_dispatches_frozen_registry_matrix() -> None:
    payload = load_workflow(CAMPAIGN)
    jobs = payload["jobs"]
    assert set(jobs) == {"prepare", "compare", "summarize"}
    prepare = jobs["prepare"]
    prepare_script = "\n".join(
        step.get("run", "") for step in prepare["steps"] if isinstance(step, dict)
    )
    assert "roundup_crypto_lab.historical_scenarios" in prepare_script
    assert "roundup_crypto_lab.historical_campaign matrix" in prepare_script

    compare = jobs["compare"]
    assert compare["strategy"]["fail-fast"] == "false"
    assert compare["strategy"]["max-parallel"] == "2"
    assert compare["uses"] == "./.github/workflows/all-strategy-comparison.yml"
    assert compare["with"]["scenario_id"] == "${{ matrix.scenario_id }}"
    assert compare["with"]["artifact_name"] == "${{ matrix.artifact_name }}"


def test_campaign_summary_runs_even_after_matrix_failure() -> None:
    payload = load_workflow(CAMPAIGN)
    summarize = payload["jobs"]["summarize"]
    assert summarize["if"] == "always()"
    assert summarize["needs"] == ["prepare", "compare"]
    scripts = "\n".join(
        step.get("run", "") for step in summarize["steps"] if isinstance(step, dict)
    )
    assert "historical_campaign summary" in scripts
    assert "historical-campaign-summary.md" in scripts


def test_single_scenario_workflow_remains_dispatchable_and_reusable() -> None:
    payload = load_workflow(COMPARISON)
    triggers = payload["on"]
    assert "workflow_dispatch" in triggers
    assert "workflow_call" in triggers
    call_inputs = triggers["workflow_call"]["inputs"]
    assert set(call_inputs) >= {
        "timerange",
        "timeframe",
        "pair",
        "capital_mode",
        "initial_capital",
        "monthly_budget",
        "contribution_day",
        "fee_ratio",
        "scenario_id",
        "registry_id",
        "artifact_name",
    }
    steps = payload["jobs"]["compare-all-strategies"]["steps"]
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    assert "Generate, validate, and summarize comparison" in names
    assert "Record scenario status" in names
    upload = next(
        step
        for step in steps
        if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert upload["if"] == "always()"
    assert "inputs.artifact_name" in upload["with"]["name"]
