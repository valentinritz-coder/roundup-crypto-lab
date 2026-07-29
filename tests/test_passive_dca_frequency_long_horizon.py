from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.passive_dca_frequency_campaign import load_json, plan_campaign
from roundup_crypto_lab.passive_dca_frequency_long_horizon import (
    EXPECTED_SCENARIOS,
    EXPECTED_STRATEGY_RESULTS,
    EXPECTED_TIMERANGE,
    _aggregate_phase_trajectories,
    _trajectory_metrics,
    load_long_horizon_study,
    materialize_long_horizon_campaign,
    validate_long_horizon_protocol,
)
from roundup_crypto_lab.passive_dca_frequency_research import load_research_protocol

STUDY_PATH = Path("config/passive-dca-frequency-long-horizon.json")
WORKFLOW_PATH = Path(".github/workflows/passive-dca-frequency-long-horizon.yml")


def _inputs():
    study = load_long_horizon_study(STUDY_PATH)
    research = load_research_protocol(Path(study["research_path"]))
    campaign = materialize_long_horizon_campaign(study, research)
    registry = load_registry(Path(research["registry_path"]))
    policy = load_json(Path(research["policy_path"]))
    return study, research, campaign, registry, policy


def test_committed_long_horizon_study_materializes_exact_matrix() -> None:
    study, research, campaign, registry, policy = _inputs()
    validation = validate_long_horizon_protocol(
        study,
        research,
        campaign,
        registry,
        policy,
        cost_profile_dir=Path(research["cost_profile_dir"]),
    )
    rows = plan_campaign(campaign)
    assert validation["scenario_count"] == EXPECTED_SCENARIOS
    assert validation["strategy_result_count"] == EXPECTED_STRATEGY_RESULTS
    assert len(rows) == EXPECTED_SCENARIOS
    assert {row["timerange"] for row in rows} == {EXPECTED_TIMERANGE}
    assert {row["phase"] for row in rows} == {"exploratory"}
    assert {row["window_set_id"] for row in rows} == {"continuous-90m"}
    assert campaign["window_sets"] == [
        {
            "window_set_id": "continuous-90m",
            "phase": "exploratory",
            "start": "20180701",
            "end": "20260101",
            "months": 90,
            "step_months": 90,
            "overlapping": False,
        }
    ]


def _curve(values: list[str]) -> list[dict[str, object]]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        {
            "timestamp": (start + timedelta(hours=4 * index)).isoformat(),
            "cash_balance": "0",
            "crypto_value": value,
            "portfolio_value": value,
            "cumulative_contributions": "100",
            "capital_invested": "100",
            "cumulative_fees_paid": "0",
        }
        for index, value in enumerate(values)
    ]


def test_phase_trajectory_aggregation_uses_median_never_best_phase() -> None:
    rows = _aggregate_phase_trajectories(
        [
            {"equity_curve": _curve(["90", "110", "130"])},
            {"equity_curve": _curve(["100", "120", "140"])},
            {"equity_curve": _curve(["1000", "1000", "1000"])},
        ]
    )
    assert [row["portfolio_value"] for row in rows] == [
        Decimal("100"),
        Decimal("120"),
        Decimal("140"),
    ]
    assert all(row["capital_deployment_ratio"] == Decimal("1") for row in rows)


def test_trajectory_metrics_measure_path_not_only_terminal_value() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for index, difference in enumerate(("-10", "-5", "2", "-1")):
        rows.append(
            {
                "timestamp": start + timedelta(hours=4 * index),
                "difference_vs_monthly": Decimal(difference),
                "capital_deployment_ratio": Decimal("0.9"),
                "cash_balance": Decimal("10"),
            }
        )
    metrics = _trajectory_metrics("weekly", rows)
    assert metrics["observations_below_monthly"] == 3
    assert metrics["time_below_monthly_ratio"] == Decimal("0.75")
    assert metrics["worst_intermediate_difference_vs_monthly"] == Decimal("-10")
    assert metrics["best_intermediate_difference_vs_monthly"] == Decimal("2")
    assert metrics["maximum_consecutive_days_below_monthly"] == Decimal(8) / Decimal(24)
    assert metrics["ending_difference_vs_monthly"] == Decimal("-1")


def test_long_horizon_workflow_is_dispatchable_and_four_way_sharded() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch) == {"study_path", "artifact_name"}
    assert set(workflow["jobs"]) == {"prepare", "validate", "aggregate"}
    assert "Passive DCA frequency long-horizon validation" in text
    assert "20180701-20260101" in text
    assert "passive_dca_frequency_long_horizon" in text
    assert "passive_dca_frequency_scenario" in text
    assert "fromJSON(needs.prepare.outputs.shards)" in text
    assert "max-parallel: 4" in text
    assert "long-horizon-summary.md" in text
    assert "start_date" not in dispatch
    assert "phase" not in dispatch
