from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/dca-controlled-comparison.yml")


def test_controlled_dca_workflow_contract() -> None:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    dispatch = payload[True]["workflow_dispatch"]["inputs"]
    assert set(dispatch) == {
        "pair",
        "timeframe",
        "timerange",
        "registry_path",
        "initial_capital",
        "monthly_budget",
        "contribution_day",
        "fee_ratio",
        "artifact_name",
    }
    job = payload["jobs"]["compare-dca-strategies"]
    rendered = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/cache/restore@v4" in rendered
    assert "No prepared Kraken cache was restored" in rendered
    assert "python -m roundup_crypto_lab.dca_registry" in rendered
    assert "python -m roundup_crypto_lab.dca_controlled_comparison" in rendered
    assert "if: always()" in rendered
    assert "actions/upload-artifact@v4" in rendered
    assert job["runs-on"] == "ubuntu-latest"
