from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from roundup_crypto_lab.short_delay_campaign import (
    PROFILES,
    STRATEGIES,
    aggregate_campaign,
    campaign_provenance,
    load_campaign,
    plan_campaign,
)

CAMPAIGN_PATH = Path("config/short-delay-dca-campaign.json")
WORKFLOW_PATH = Path(".github/workflows/short-delay-dca-research.yml")


def test_committed_campaign_has_exact_frozen_matrix() -> None:
    campaign = load_campaign(CAMPAIGN_PATH)

    assert tuple(campaign["strategy_ids"]) == STRATEGIES
    assert tuple(campaign["cost_profiles"]) == PROFILES
    assert len(plan_campaign(campaign, "multi-window")) == 48
    assert len(plan_campaign(campaign, "historical-complement")) == 4


def test_known_kraken_gap_is_excluded_before_planning() -> None:
    campaign = load_campaign(CAMPAIGN_PATH)
    rows = [
        *plan_campaign(campaign, "multi-window"),
        *plan_campaign(campaign, "historical-complement"),
    ]

    assert all("20180101" not in row["timerange"] for row in rows)
    assert len(rows) == 52
    assert len(rows) * len(STRATEGIES) == 208


def test_protocol_and_strategy_registry_digests_are_stable() -> None:
    provenance = campaign_provenance(load_campaign(CAMPAIGN_PATH))

    assert provenance["protocol_digest"].startswith("sha256:")
    assert provenance["strategy_registry_digest"].startswith("sha256:")
    assert provenance["maximum_delay_calendar_days"] == 7


def test_campaign_rejects_extra_strategy(tmp_path: Path) -> None:
    payload = json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))
    payload["strategy_ids"].append("optimized_variant")
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="four frozen strategies"):
        load_campaign(path)


def test_incomplete_matrix_blocks_aggregation(tmp_path: Path) -> None:
    campaign = load_campaign(CAMPAIGN_PATH)

    with pytest.raises(ValueError, match="aggregation blocked"):
        aggregate_campaign(
            campaign=campaign,
            section="historical-complement",
            result_files=(),
            repository_commit="deadbeef",
            output_dir=tmp_path,
        )

    status = json.loads((tmp_path / "coverage-report.json").read_text(encoding="utf-8"))
    assert status["matrix_complete"] is False
    assert status["ranking_allowed"] is False
    assert len(status["missing_scenario_ids"]) == 4


def test_workflow_dispatch_and_sharding_contract() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]

    assert set(inputs) == {"campaign_path", "research_section", "artifact_prefix"}
    assert "lookback" not in text
    assert "moving_average" not in text
    assert "maximum_delay" not in text
    assert "actions/cache/restore@v4" in text
    assert "fail-fast: false" in text
    assert "exit \"$failed\"" in text
    assert "needs.research.result == 'success'" in text
    assert "incomplete; final aggregation and ranking blocked" in text


def test_workflow_contains_exact_scenario_counts() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'expected = 48 if os.environ["RESEARCH_SECTION"] == "multi-window" else 4' in text
    assert "short-delay-scenario.json" not in text
    assert "short_delay_campaign" in text
