from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundup_crypto_lab.dca_robustness_campaign import (
    aggregate_campaign,
    materialize_registry,
    plan_campaign,
    write_outputs,
)


def _campaign() -> dict:
    return {
        "campaign_id": "test",
        "pairs": ["BTC/EUR"],
        "window_sets": [
            {
                "window_set_id": "rolling",
                "phase": "exploratory",
                "start": "20200101",
                "end": "20250101",
                "months": 24,
                "step_months": 12,
                "overlapping": True,
            },
            {
                "window_set_id": "holdout",
                "phase": "confirmation",
                "start": "20230101",
                "end": "20250101",
                "months": 24,
                "step_months": 24,
                "overlapping": False,
            },
        ],
        "parameter_neighborhoods": [],
        "disclosures": ["Overlapping rolling windows are not independent observations."],
    }


def _policy() -> dict:
    return {
        "policy_id": "policy",
        "control": "MonthlyDCA",
        "thresholds": {
            "minimum_evaluated_windows": 2,
            "robust_improvement_final_value_win_rate": 0.70,
            "robust_improvement_quantity_win_rate": 0.55,
            "promising_final_value_win_rate": 0.55,
            "cash_heavy_median_deployment_ratio_max": 0.75,
            "inactive_no_buy_rate": 0.5,
            "unstable_rank_dispersion_min": 2.0,
            "rejected_final_value_win_rate_max": 0.35,
        },
    }


def _result(path: Path, timerange: str, strategy_value: str, deployment: str, actions: int) -> None:
    payload = {
        "scenario": {"pair": "BTC/EUR", "timerange": timerange},
        "registry": {"registry_digest": "sha256:registry"},
        "campaign": {
            "window_set_id": "rolling",
            "phase": "exploratory",
            "variant_id": "frozen-default",
        },
        "comparison": [
            {
                "method": "MonthlyDCA",
                "final_value": "100",
                "profit": "0",
                "final_crypto_quantity": "1",
                "capital_deployment_ratio": "1",
                "oldest_retained_cash_age_seconds": "0",
                "fees": "1",
                "action_count": 2,
                "xirr": "0",
                "twr": "0",
                "raw_drawdown": "0.2",
            },
            {
                "method": "Pilot",
                "final_value": strategy_value,
                "profit": str(float(strategy_value) - 100),
                "final_crypto_quantity": "1.1",
                "capital_deployment_ratio": deployment,
                "oldest_retained_cash_age_seconds": "100",
                "fees": "2",
                "action_count": actions,
                "xirr": "0.1",
                "twr": "0.1",
                "raw_drawdown": "0.1",
            },
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_plan_discloses_overlapping_and_separates_holdout() -> None:
    rows = plan_campaign(_campaign())
    assert any(row["overlapping"] for row in rows)
    assert {row["phase"] for row in rows} == {"exploratory", "confirmation"}
    assert len({(row["pair"], row["window_set_id"], row["timerange"]) for row in rows}) == len(rows)


def test_stable_cash_heavy_and_inactive_classifications(tmp_path: Path) -> None:
    campaign = _campaign()
    files = []
    for index, timerange in enumerate(("20200101-20220101", "20210101-20230101", "20220101-20240101")):
        path = tmp_path / f"result-{index}.json"
        _result(path, timerange, "110", "0.60", 2)
        files.append(path)
    payload = aggregate_campaign(
        campaign=campaign,
        policy=_policy(),
        result_files=files,
        registry_digest="sha256:registry",
        repository_commit="a" * 40,
    )
    pilot = next(row for row in payload["aggregate_statistics"] if row["strategy"] == "Pilot")
    assert pilot["classification"] == "promising but cash-heavy"
    for path in files:
        data = json.loads(path.read_text())
        data["comparison"][1]["action_count"] = 0
        data["comparison"][1]["capital_deployment_ratio"] = "0.1"
        path.write_text(json.dumps(data))
    inactive = aggregate_campaign(
        campaign=campaign,
        policy=_policy(),
        result_files=files,
        registry_digest="sha256:registry",
        repository_commit="a" * 40,
    )
    pilot = next(row for row in inactive["aggregate_statistics"] if row["strategy"] == "Pilot")
    assert pilot["classification"] == "inactive"


def test_regime_dependent_and_rejected_are_not_promoted(tmp_path: Path) -> None:
    files = []
    for index, (timerange, value) in enumerate(
        (("20200101-20220101", "110"), ("20210101-20230101", "105"), ("20220101-20240101", "90"))
    ):
        path = tmp_path / f"result-{index}.json"
        _result(path, timerange, value, "1", 3)
        files.append(path)
    payload = aggregate_campaign(
        campaign=_campaign(),
        policy=_policy(),
        result_files=files,
        registry_digest="sha256:registry",
        repository_commit="a" * 40,
    )
    pilot = next(row for row in payload["aggregate_statistics"] if row["strategy"] == "Pilot")
    assert pilot["classification"] == "regime-dependent"
    for path in files:
        data = json.loads(path.read_text())
        data["comparison"][1]["final_value"] = "90"
        path.write_text(json.dumps(data))
    rejected = aggregate_campaign(
        campaign=_campaign(),
        policy=_policy(),
        result_files=files,
        registry_digest="sha256:registry",
        repository_commit="a" * 40,
    )
    pilot = next(row for row in rejected["aggregate_statistics"] if row["strategy"] == "Pilot")
    assert pilot["classification"] == "rejected"


def test_parameter_neighborhood_keeps_frozen_default_immutable() -> None:
    registry = {
        "registry_id": "registry",
        "strategies": [
            {"strategy_id": "pilot", "parameters": {"threshold": "0.5"}},
        ],
    }
    variant = materialize_registry(registry, "pilot", {"threshold": "0.6"})
    assert registry["strategies"][0]["parameters"]["threshold"] == "0.5"
    assert variant["strategies"][0]["parameters"]["threshold"] == "0.6"
    with pytest.raises(ValueError):
        materialize_registry(registry, "pilot", {"observed_best": "1"})


def test_duplicate_and_incompatible_scenarios_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    _result(path, "20200101-20220101", "110", "1", 2)
    with pytest.raises(ValueError, match="duplicate scenario result"):
        aggregate_campaign(
            campaign=_campaign(),
            policy=_policy(),
            result_files=[path, path],
            registry_digest="sha256:registry",
            repository_commit="a" * 40,
        )
    data = json.loads(path.read_text())
    data["scenario"]["pair"] = "ETH/EUR"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="incompatible campaign scenarios"):
        aggregate_campaign(
            campaign=_campaign(),
            policy=_policy(),
            result_files=[path],
            registry_digest="sha256:registry",
            repository_commit="a" * 40,
        )


def test_identical_inputs_produce_byte_stable_outputs(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    _result(path, "20200101-20220101", "110", "1", 2)
    payload = aggregate_campaign(
        campaign=_campaign(),
        policy=_policy(),
        result_files=[path],
        registry_digest="sha256:registry",
        repository_commit="a" * 40,
    )
    write_outputs(payload, tmp_path / "one")
    write_outputs(payload, tmp_path / "two")
    for name in (
        "dca-robustness-campaign.json",
        "dca-robustness-campaign.csv",
        "dca-robustness-report.md",
        "dca-robustness-survivors.json",
    ):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
