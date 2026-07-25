from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/seed-kraken-data.yml"


def load_workflow() -> dict[str, object]:
    payload = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_seed_workflow_accepts_explicit_start_date() -> None:
    payload = load_workflow()
    inputs = payload["on"]["workflow_dispatch"]["inputs"]
    start_date = inputs["start_date"]
    assert start_date["required"] == "true"
    assert start_date["default"] == "20240401"


def test_seed_workflow_uses_release_asset_not_trade_backfill() -> None:
    payload = load_workflow()
    steps = payload["jobs"]["seed"]["steps"]
    scripts = "\n".join(step.get("run", "") for step in steps if isinstance(step, dict))
    assert "gh release download" in scripts
    assert "kraken-ohlcv-import seed.zip" in scripts
    assert "roundup_crypto_lab.kraken_seed_window" in scripts
    assert "--start-date" in scripts
    assert "download-data" not in scripts
    assert "--dl-trades" not in scripts


def test_seed_cache_identity_includes_requested_start_date() -> None:
    payload = load_workflow()
    steps = payload["jobs"]["seed"]["steps"]
    save = next(step for step in steps if step.get("uses") == "actions/cache/save@v4")
    assert "inputs.start_date" in save["with"]["key"]
