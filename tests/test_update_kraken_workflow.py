from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/update-kraken-data.yml")


def load_workflow() -> dict[str, object]:
    payload = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_weekly_update_uses_direct_bounded_ohlcv_only() -> None:
    payload = load_workflow()
    steps = payload["jobs"]["update"]["steps"]
    scripts = "\n".join(step.get("run", "") for step in steps if isinstance(step, dict))

    assert "roundup_crypto_lab.kraken_update_plan" in scripts
    assert "--days \"$DAYS\"" in scripts
    assert "--timeframes 4h" in scripts
    assert "--dl-trades" not in scripts
    assert "--timerange" not in scripts
    assert "tmp-kraken-ohlcv" in scripts
    assert "tmp-kraken-trades" not in scripts


def test_download_step_has_bounded_timeout_and_seed_rebuild_message() -> None:
    payload = load_workflow()
    steps = payload["jobs"]["update"]["steps"]
    download = next(
        step for step in steps if step.get("name") == "Download recent Kraken OHLCV overlap"
    )
    plan = next(
        step for step in steps if step.get("name") == "Plan bounded weekly OHLCV update"
    )

    assert download["timeout-minutes"] == "20"
    assert "Run Seed Kraken data" in plan["run"]
    assert "historical_rebuild_required" not in download["run"]
