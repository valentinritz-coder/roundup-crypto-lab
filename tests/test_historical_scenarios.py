import copy
import json
from pathlib import Path

import pytest

from roundup_crypto_lab.historical_scenarios import (
    DEFAULT_REGISTRY,
    HistoricalScenarioRegistry,
    canonical_json,
    load_registry,
)


def registry_payload() -> dict[str, object]:
    return json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))


def test_frozen_registry_contains_eight_expected_scenarios() -> None:
    registry = load_registry()
    assert registry.registry_id == "historical-one-shot-2024-2026-v1"
    assert len(registry.scenarios) == 8
    assert {scenario.pair for scenario in registry.scenarios} == {"BTC/EUR", "ETH/EUR"}
    assert {scenario.timeframe for scenario in registry.scenarios} == {"4h"}
    assert {scenario.capital_mode for scenario in registry.scenarios} == {
        "one_shot_capital"
    }
    assert {scenario.timerange for scenario in registry.scenarios} == {
        "20240401-20241001",
        "20241001-20250401",
        "20250401-20251001",
        "20251001-20260125",
    }


def test_registry_order_and_canonical_json_are_deterministic() -> None:
    payload = registry_payload()
    reversed_payload = copy.deepcopy(payload)
    reversed_payload["scenarios"] = list(reversed(reversed_payload["scenarios"]))
    first = HistoricalScenarioRegistry.from_mapping(payload)
    second = HistoricalScenarioRegistry.from_mapping(reversed_payload)
    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert [row.scenario_id for row in first.scenarios] == sorted(
        row.scenario_id for row in first.scenarios
    )


def test_decimal_representations_canonicalize_without_changing_identity() -> None:
    payload = registry_payload()
    payload["scenarios"] = [payload["scenarios"][0]]
    alternate = copy.deepcopy(payload)
    alternate["scenarios"][0]["initial_capital"] = 40.0
    alternate["scenarios"][0]["monthly_budget"] = "40.00"
    alternate["scenarios"][0]["fee_ratio"] = "0.002600"
    first = HistoricalScenarioRegistry.from_mapping(payload)
    second = HistoricalScenarioRegistry.from_mapping(alternate)
    assert first == second
    row = second.canonical_mapping()["scenarios"][0]
    assert row["initial_capital"] == "40"
    assert row["monthly_budget"] == "40"
    assert row["fee_ratio"] == "0.0026"


def test_duplicate_ids_and_duplicate_definitions_fail() -> None:
    duplicate_id = registry_payload()
    duplicate_id["scenarios"][1]["scenario_id"] = duplicate_id["scenarios"][0][
        "scenario_id"
    ]
    with pytest.raises(ValueError, match="duplicate scenario id"):
        HistoricalScenarioRegistry.from_mapping(duplicate_id)

    duplicate_definition = registry_payload()
    duplicate_definition["scenarios"][1] = copy.deepcopy(
        duplicate_definition["scenarios"][0]
    )
    duplicate_definition["scenarios"][1]["scenario_id"] = "different-id"
    with pytest.raises(ValueError, match="duplicate scenario definition"):
        HistoricalScenarioRegistry.from_mapping(duplicate_definition)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scenario_id", "BTC invalid", "kebab-case"),
        ("pair", "DOGE/EUR", "unsupported pair"),
        ("timeframe", "1h", "unsupported timeframe"),
        ("timerange", "2024-01-01-2024-02-01", "strict YYYYMMDD"),
        ("timerange", "20240230-20240301", "invalid calendar date"),
        ("timerange", "20241001-20240401", "strictly before"),
        ("capital_mode", "invented_mode", "unsupported capital mode"),
        ("contribution_day", True, "must be an integer"),
        ("initial_capital", float("nan"), "must be positive"),
        ("fee_ratio", float("inf"), "must be non-negative"),
        ("regime_label", "", "non-empty string"),
    ],
)
def test_invalid_scenario_fields_fail(field: str, value: object, message: str) -> None:
    payload = registry_payload()
    payload["scenarios"] = [payload["scenarios"][0]]
    payload["scenarios"][0][field] = value
    with pytest.raises(ValueError, match=message):
        HistoricalScenarioRegistry.from_mapping(payload)


def test_overlaps_require_explicit_reason_on_both_rows() -> None:
    payload = registry_payload()
    payload["scenarios"] = payload["scenarios"][:2]
    payload["scenarios"][1]["timerange"] = "20240901-20250401"
    with pytest.raises(ValueError, match="require overlap_reason on both rows"):
        HistoricalScenarioRegistry.from_mapping(payload)

    payload["scenarios"][0]["overlap_reason"] = "intentional robustness overlap"
    payload["scenarios"][1]["overlap_reason"] = "intentional robustness overlap"
    registry = HistoricalScenarioRegistry.from_mapping(payload)
    assert len(registry.scenarios) == 2


def test_adjacent_windows_and_cross_pair_overlaps_are_valid() -> None:
    registry = load_registry()
    assert len(registry.scenarios) == 8


def test_load_and_output_file_are_strict_json(tmp_path: Path) -> None:
    registry = load_registry()
    output = tmp_path / "canonical.json"
    output.write_text(canonical_json(registry), encoding="utf-8")
    reloaded = HistoricalScenarioRegistry.from_mapping(
        json.loads(output.read_text(encoding="utf-8"))
    )
    assert reloaded == registry
    assert "NaN" not in output.read_text(encoding="utf-8")
    assert "Infinity" not in output.read_text(encoding="utf-8")
