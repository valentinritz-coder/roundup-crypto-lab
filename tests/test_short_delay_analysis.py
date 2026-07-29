from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from roundup_crypto_lab.short_delay_analysis import _qualifies, load_policy

POLICY = Path("config/short-delay-dca-decision-policy.json")
WORKFLOW = Path(".github/workflows/short-delay-dca-final-analysis.yml")


def summary(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "median_terminal_value_difference_ratio": Decimal("0.002"),
        "window_win_rate": Decimal("0.75"),
        "positive_window_set_count": 3,
        "worst_terminal_value_difference_ratio": Decimal("-0.002"),
        "contribution_btc_improvement_rate": Decimal("0.70"),
        "median_btc_quantity_difference": Decimal("0.0001"),
        "average_forced_release_rate": Decimal("0.20"),
        "long_horizon_terminal_value_difference_ratio": Decimal("0.003"),
        "long_horizon_btc_quantity_difference": Decimal("0.0002"),
    }
    value.update(overrides)
    return value


def test_committed_policy_is_strict_and_frozen() -> None:
    policy = load_policy(POLICY)

    assert policy["control_strategy_id"] == "monthly_dca_control"
    assert policy["candidate_strategy_ids"] == [
        "negative_7d_return_delay",
        "below_7d_sma_delay",
        "confirmed_short_decline_delay",
    ]
    assert policy["primary_cost_profile_id"] == "proportional-plus-spread-v1"
    assert policy["minimum_positive_window_set_count"] == 3
    assert policy["fallback_decision"] == "retain_monthly_dca"


def test_rule_qualifies_only_when_every_guardrail_passes() -> None:
    passed, failures = _qualifies(summary(), load_policy(POLICY))

    assert passed is True
    assert failures == []


@pytest.mark.parametrize(
    ("overrides", "expected_failure"),
    [
        (
            {"median_terminal_value_difference_ratio": Decimal("0.0009")},
            "meaningful_net_improvement",
        ),
        ({"window_win_rate": Decimal("0.59")}, "broad_window_win_rate"),
        ({"positive_window_set_count": 2}, "all_window_sets_positive"),
        (
            {"worst_terminal_value_difference_ratio": Decimal("-0.006")},
            "worst_case_guardrail",
        ),
        (
            {"median_btc_quantity_difference": Decimal("0")},
            "btc_improvement",
        ),
        (
            {"average_forced_release_rate": Decimal("0.51")},
            "limited_forced_release",
        ),
        (
            {"long_horizon_terminal_value_difference_ratio": Decimal("0")},
            "positive_long_horizon_value",
        ),
        (
            {"long_horizon_btc_quantity_difference": Decimal("0")},
            "positive_long_horizon_btc",
        ),
    ],
)
def test_each_failed_guardrail_blocks_adoption(
    overrides: dict[str, object],
    expected_failure: str,
) -> None:
    passed, failures = _qualifies(summary(**overrides), load_policy(POLICY))

    assert passed is False
    assert expected_failure in failures


def test_risk_reduction_cannot_replace_return_or_btc_improvement() -> None:
    passed, failures = _qualifies(
        summary(
            median_terminal_value_difference_ratio=Decimal("-0.001"),
            median_btc_quantity_difference=Decimal("-0.0001"),
        ),
        load_policy(POLICY),
    )

    assert passed is False
    assert "meaningful_net_improvement" in failures
    assert "btc_improvement" in failures


def test_policy_rejects_candidate_or_primary_profile_drift(tmp_path: Path) -> None:
    payload = json.loads(POLICY.read_text(encoding="utf-8"))
    payload["candidate_strategy_ids"].append("optimized_combination")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="strategy set"):
        load_policy(path)


def test_final_workflow_runs_exact_full_matrix_without_signal_inputs() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]

    assert set(dispatch) == {"campaign_path", "policy_path", "artifact_prefix"}
    assert "lookback" not in raw
    assert "moving_average" not in raw
    assert "delay_duration" not in raw
    assert "expected = 52" in raw
    assert "short_delay_analysis" in raw
    assert "actions/cache/restore@v4" in raw
    assert "fail-fast: false" in raw
    assert "exit \"$failed\"" in raw
    assert "final-conclusion.json" in raw
