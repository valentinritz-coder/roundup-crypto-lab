from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def content(name):
    return (ROOT / ".github/workflows" / name).read_text()


def test_workflows_parse_and_cache_roles() -> None:
    seed, update, validation = (
        content(x)
        for x in ("seed-kraken-data.yml", "update-kraken-data.yml", "freqtrade-validation.yml")
    )
    for text in (seed, update, validation):
        yaml.safe_load(text)
        assert "--erase" not in text
    assert "actions/cache/restore@v4" not in seed and "actions/cache/save@v4" in seed
    assert "actions/cache/restore@v4" in update and "actions/cache/save@v4" in update
    assert "actions/cache/restore@v4" in validation and "actions/cache/save@v4" not in validation
    assert all("kraken-ohlcv-v1-" in text for text in (seed, update, validation))


def test_workflow_safety_contract() -> None:
    seed, update, validation = (
        content(x)
        for x in ("seed-kraken-data.yml", "update-kraken-data.yml", "freqtrade-validation.yml")
    )
    assert "--days 8" in update and "--dl-trades" in update
    assert "BTC/EUR ETH/EUR" in update and "--timeframes 4h" in update
    assert "Changing state.*RUNNING" in validation and "PIPESTATUS" not in validation
    assert "path: |\n            artifacts/" in seed


def test_seed_uses_non_strict_validation_and_post_update_workflows_remain_strict() -> None:
    seed, update, validation = (
        content(x)
        for x in ("seed-kraken-data.yml", "update-kraken-data.yml", "freqtrade-validation.yml")
    )
    assert "seed_history_timerange" in seed and "report_gaps" in seed
    assert "common_timerange" not in seed
    assert "common_timerange" in update and "common_timerange" in validation
    assert "--timerange" in update and "--days 8" in update
    assert "strftime('%Y%m%d')" not in update
    assert all("python -m pip check" in text for text in (seed, update, validation))


def test_freqtrade_validation_analysis_contract() -> None:
    validation = content("freqtrade-validation.yml")
    assert 'defaults: {run: {shell: "bash -eo pipefail {0}"}}' in validation
    assert validation.count("--config user_data/config.json") >= 6
    lookahead = validation.split("- name: Run lookahead analysis", 1)[1].split(
        "- name: Validate lookahead report", 1
    )[0]
    assert "--config user_data/config-lookahead.json" in lookahead
    for command in ("backtesting", "lookahead-analysis", "recursive-analysis"):
        section = validation.split(command, 1)[1].split("\n      - name:", 1)[0]
        assert "--timerange" in section
    for name, log in (
        ("baseline backtest", "backtest.txt"),
        ("lookahead", "lookahead.txt"),
        ("recursive", "recursive.txt"),
    ):
        assert f"Run {name}" in validation and f"artifacts/logs/{log}" in validation
    assert "Validate lookahead report" in validation and "Validate recursive report" in validation
    assert "if: always()" in validation and "actions/upload-artifact@v4" in validation


def test_breakout_comparison_workflow_contract() -> None:
    workflow = content("breakout-strategy-comparison.yml")
    parsed = yaml.safe_load(workflow)
    assert parsed
    assert "workflow_dispatch:" in workflow
    assert "pull_request" not in workflow and "push:" not in workflow
    assert "timerange:" in workflow and "validate-timerange" in workflow
    assert "validate-data" in workflow and "Update Kraken data first" in workflow
    assert "actions/cache/restore@v4" in workflow
    assert "download-data" not in workflow and "--erase" not in workflow
    assert "hyperopt" not in workflow
    for strategy in (
        "RoundupBreakoutStrategy",
        "RoundupBreakoutTrendStrategy",
        "RoundupBreakoutAtrStrategy",
        "RoundupBreakoutAtrVolumeStrategy",
    ):
        assert workflow.count(f"--strategy {strategy}") == 1
        assert f"--result {strategy}=" in workflow
    # The fifth use is list-data; every one of the four backtests shares this config.
    assert workflow.count("--config user_data/config.json") >= 4
    assert workflow.count('--timeframe "$TIMEFRAME"') >= 4
    assert workflow.count('--timerange "$TIMERANGE"') >= 4
    for filename in ("baseline.zip", "trend.zip", "atr.zip", "atr-volume.zip"):
        assert f"artifacts/results/{filename}" in workflow
    assert "python -m roundup_crypto_lab.compare_strategies" in workflow
    assert "breakout-comparison.json" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "actions/upload-artifact@v4" in workflow and "if: always()" in workflow
