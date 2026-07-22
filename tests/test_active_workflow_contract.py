from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/all-strategy-comparison.yml")
STRATEGIES = (
    "RoundupBreakoutStrategy",
    "RoundupBreakoutTrendStrategy",
    "RoundupBreakoutAtrStrategy",
    "RoundupBreakoutAtrVolumeStrategy",
    "RoundupTrendPullbackStrategy",
    "RoundupConfirmedBreakoutStrategy",
    "RoundupVolatilitySqueezeStrategy",
)


def test_all_strategy_workflow_executes_and_consumes_effective_arguments() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    steps = workflow["jobs"]["compare-all-strategies"]["steps"]
    by_name = {step.get("name"): step for step in steps if step.get("name")}

    validate = by_name["Validate inputs and immutable prepared data"]["run"]
    assert '"dry_run_wallet": int(os.environ["INITIAL_CAPITAL"])' in validate
    assert '"fee": float(os.environ["FEE"])' in validate
    assert "artifacts/pair-config.json" in validate

    backtests = by_name["Run seven equivalent backtests"]["run"]
    assert "--config artifacts/pair-config.json" in backtests
    for strategy in STRATEGIES:
        assert backtests.count(f"run_backtest {strategy} ") == 1

    active = by_name["Generate and validate active results"]["run"]
    for argument in (
        '--pair "$PAIR"',
        '--timerange "$TIMERANGE"',
        '--capital-mode "$CAPITAL_MODE"',
        '--initial-capital "$INITIAL_CAPITAL"',
        '--monthly-budget "$MONTHLY_BUDGET"',
        '--contribution-day "$CONTRIBUTION_DAY"',
        '--fee "$FEE"',
        "--config-file artifacts/pair-config.json",
    ):
        assert argument in active

    differential = by_name["Validate one-shot native differential"]
    assert differential["if"] == "${{ inputs.capital_mode == 'one_shot_capital' }}"
    command = differential["run"]
    assert "one_shot_differential combine" in command
    assert "assert len" not in command
    for strategy in STRATEGIES:
        assert f"differential-{strategy}.json" in command

    report = by_name["Generate, validate, and summarize comparison"]["run"]
    assert '--config-digest "$CONFIG_DIGEST"' in report
    assert "--one-shot-differential artifacts/results/one-shot-differential.json" in report
    for strategy in STRATEGIES:
        assert f"--active-result artifacts/results/active-{strategy}.json" in report

    upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")
    assert upload["if"] == "success()"
    assert upload["with"]["if-no-files-found"] == "error"
