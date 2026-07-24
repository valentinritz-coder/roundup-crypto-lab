import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def content(name):
    return (ROOT / ".github/workflows" / name).read_text()


def cache_restore_settings(workflow: str) -> tuple[str, str, str]:
    match = re.search(
        r"actions/cache/restore@v4.*?with: \{path: ([^,]+), "
        r"key: '([^$]+)\$\{\{ github.run_id \}\}', restore-keys: ([^}]+)\}",
        workflow,
        flags=re.DOTALL,
    )
    assert match
    return match.groups()


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


def test_all_strategy_comparison_workflow_contract() -> None:
    workflow = content("all-strategy-comparison.yml")
    parsed = yaml.safe_load(workflow)
    assert parsed and "workflow_dispatch:" in workflow
    assert all(trigger not in workflow for trigger in ("push:", "pull_request", "schedule:"))
    assert "required: true" in workflow and 'options: ["4h"]' in workflow
    assert "download-data" not in workflow and "hyperopt" not in workflow
    assert "--export-filename" not in workflow and "--cache none" in workflow
    assert "kraken-ohlcv-pipeline" in workflow
    strategies = (
        "RoundupBreakoutStrategy",
        "RoundupBreakoutTrendStrategy",
        "RoundupBreakoutAtrStrategy",
        "RoundupBreakoutAtrVolumeStrategy",
        "RoundupTrendPullbackStrategy",
        "RoundupConfirmedBreakoutStrategy",
        "RoundupVolatilitySqueezeStrategy",
        "RoundupScientificControlBreakoutStrategy",
        "RoundupRiskAdjustedMomentumStrategy",
        "RoundupBullPullbackRsiStrategy",
        "RoundupDistanceReversionStrategy",
    )
    for name in strategies:
        assert workflow.count(f"run_backtest {name}") == 1
        assert f"--result {name}=" in workflow
        assert f"--active-result artifacts/results/active-{name}.json" in workflow
    assert "Run eleven equivalent backtests" in workflow
    assert workflow.count("--config artifacts/pair-config.json") >= 1
    backtests = workflow.split("run_backtest()", 1)[1].split(
        "run_backtest RoundupBreakoutStrategy", 1
    )[0]
    assert "--backtest-directory artifacts/results" in backtests
    assert "artifacts/results/$2" not in backtests
    assert "rm -f artifacts/results/backtest-result-*.zip" in backtests
    assert "find artifacts/results -maxdepth 1 -type f -name 'backtest-result-*.zip'" in backtests
    assert "sort -nr" in backtests
    assert 'test "${#zips[@]}" -eq 1' in backtests
    assert 'mv "${zips[0]}" "artifacts/results/$result_name.zip"' in backtests
    assert "-name 'backtest-result-*.meta.json' -delete" in backtests
    assert "GITHUB_STEP_SUMMARY" in workflow and "if: always()" in workflow


def test_passive_benchmarks_workflow_contract() -> None:
    workflow = content("passive-benchmarks.yml")
    assert yaml.safe_load(workflow)
    assert "workflow_dispatch:" in workflow
    assert all(trigger not in workflow for trigger in ("push:", "pull_request", "schedule:"))
    assert "timerange:" in workflow and "required: true" in workflow
    assert 'options: ["4h"]' in workflow and 'test "$TIMEFRAME" = 4h' in workflow
    assert "BTC/EUR ETH/EUR" in workflow
    assert "download-data" not in workflow and "hyperopt" not in workflow
    assert "kraken-ohlcv-pipeline" in workflow
    update = content("update-kraken-data.yml")
    assert cache_restore_settings(workflow) == cache_restore_settings(update)
    assert "passive_benchmarks" in workflow
    assert "--output-json" in workflow and "--output-dir" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "actions/upload-artifact@v4" in workflow and "if: always()" in workflow
