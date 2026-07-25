import json
from pathlib import Path

import pytest

from roundup_crypto_lab.historical_campaign import (
    MATRIX_SCHEMA_VERSION,
    STATUS_SCHEMA_VERSION,
    build_matrix,
    build_summary,
    matrix_json,
    render_markdown,
)
from roundup_crypto_lab.historical_scenarios import load_registry


def test_matrix_contains_every_frozen_scenario_once() -> None:
    registry = load_registry()
    matrix = build_matrix(registry)
    assert matrix["schema_version"] == MATRIX_SCHEMA_VERSION
    assert matrix["registry_id"] == registry.registry_id
    rows = matrix["include"]
    assert len(rows) == 8
    assert {row["scenario_id"] for row in rows} == {
        scenario.scenario_id for scenario in registry.scenarios
    }
    assert len({row["artifact_name"] for row in rows}) == 8
    assert {row["pair"] for row in rows} == {"BTC/EUR", "ETH/EUR"}
    assert {row["timeframe"] for row in rows} == {"4h"}
    assert {row["capital_mode"] for row in rows} == {"one_shot_capital"}


def test_matrix_is_deterministic_and_strict_json() -> None:
    rendered = matrix_json(load_registry())
    assert rendered == matrix_json(load_registry())
    assert "NaN" not in rendered
    assert "Infinity" not in rendered
    payload = json.loads(rendered)
    assert [row["scenario_id"] for row in payload["include"]] == sorted(
        row["scenario_id"] for row in payload["include"]
    )


def write_status(
    directory: Path,
    scenario_id: str,
    registry_id: str,
    conclusion: str,
) -> Path:
    path = directory / scenario_id / "scenario-status.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": STATUS_SCHEMA_VERSION,
                "scenario_id": scenario_id,
                "registry_id": registry_id,
                "conclusion": conclusion,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_summary_reports_success_failure_and_missing(tmp_path: Path) -> None:
    registry = load_registry()
    first, second = registry.scenarios[:2]
    paths = [
        write_status(tmp_path, first.scenario_id, registry.registry_id, "success"),
        write_status(tmp_path, second.scenario_id, registry.registry_id, "failure"),
    ]
    summary = build_summary(registry, paths)
    conclusions = {
        row["scenario_id"]: row["conclusion"] for row in summary["scenarios"]
    }
    assert conclusions[first.scenario_id] == "success"
    assert conclusions[second.scenario_id] == "failure"
    assert list(conclusions.values()).count("missing") == 6
    markdown = render_markdown(summary)
    assert first.scenario_id in markdown
    assert "| failure |" in markdown


def test_duplicate_unknown_and_wrong_registry_statuses_fail(tmp_path: Path) -> None:
    registry = load_registry()
    scenario = registry.scenarios[0]
    first = write_status(
        tmp_path / "one",
        scenario.scenario_id,
        registry.registry_id,
        "success",
    )
    duplicate = write_status(
        tmp_path / "two",
        scenario.scenario_id,
        registry.registry_id,
        "success",
    )
    with pytest.raises(ValueError, match="duplicate campaign status"):
        build_summary(registry, [first, duplicate])

    unknown = write_status(
        tmp_path / "unknown",
        "unknown-scenario",
        registry.registry_id,
        "success",
    )
    with pytest.raises(ValueError, match="unknown scenarios"):
        build_summary(registry, [unknown])

    wrong_registry = write_status(
        tmp_path / "wrong",
        scenario.scenario_id,
        "different-registry",
        "success",
    )
    with pytest.raises(ValueError, match="registry id"):
        build_summary(registry, [wrong_registry])


def test_invalid_status_conclusion_fails(tmp_path: Path) -> None:
    registry = load_registry()
    scenario = registry.scenarios[0]
    path = write_status(
        tmp_path,
        scenario.scenario_id,
        registry.registry_id,
        "greenish",
    )
    with pytest.raises(ValueError, match="invalid conclusion"):
        build_summary(registry, [path])
