import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.dca_registry import (
    load_registry,
    loads_registry,
    main,
    parse_registry,
    strategy_provenance,
)

REPOSITORY_REGISTRY = Path("config/dca-strategy-registry.json")


def base_registry():
    return {
        "registry_schema_version": 1,
        "registry_id": "research-registry",
        "strategies": [
            {
                "strategy_id": "weekly-dca",
                "implementation": "fixed_weekly",
                "strategy_version": "1",
                "hypothesis": "A weekly baseline.",
                "decision_cadence": "weekly",
                "required_indicators": [],
                "parameters": {"weekday": 0},
                "maximum_pending_cash_age_days": None,
                "minimum_order": {"amount": "0.00", "behavior": "skip"},
                "research_status": "baseline",
            },
            {
                "strategy_id": "band-fixture",
                "implementation": "indicator_band_fixture",
                "strategy_version": "2",
                "hypothesis": "A registry-only threshold fixture.",
                "decision_cadence": "every_candle",
                "required_indicators": [
                    {
                        "name": "signal",
                        "definition_id": "fixture.signal",
                        "version": "1",
                        "warmup_candles": 20,
                        "parameters": {"window": 20},
                    }
                ],
                "parameters": {
                    "upper_threshold": "1.5000",
                    "allocation_fraction": "0.500",
                    "lower_threshold": "0.50",
                },
                "maximum_pending_cash_age_days": 30,
                "minimum_order": {"amount": Decimal("10.00"), "behavior": "skip"},
                "research_status": "exploratory",
            },
        ],
    }


def test_canonical_output_and_digest_are_byte_stable() -> None:
    first = base_registry()
    second = deepcopy(first)
    second["strategies"].reverse()
    second["strategies"][0]["parameters"] = {
        "lower_threshold": "5e-1",
        "allocation_fraction": "0.5",
        "upper_threshold": "1.5",
    }
    second["strategies"][1]["minimum_order"]["amount"] = "0"

    left = parse_registry(first)
    right = parse_registry(second)

    assert left.canonical_document_bytes() == right.canonical_document_bytes()
    assert left.digest == right.digest
    assert left.strategy("band-fixture").parameters == {
        "allocation_fraction": "0.5",
        "lower_threshold": "0.5",
        "upper_threshold": "1.5",
    }


def test_repository_registry_is_valid() -> None:
    registry = load_registry(REPOSITORY_REGISTRY)
    assert [item.strategy_id for item in registry.strategies] == [
        "daily-dca",
        "monthly-dca",
        "weekly-dca",
    ]
    assert registry.strategy("weekly-dca").parameters == {"weekday": 0}


def test_cli_emits_the_canonical_repository_registry(tmp_path) -> None:
    output = tmp_path / "canonical.json"
    main([str(REPOSITORY_REGISTRY), "--output", str(output)])

    registry = load_registry(REPOSITORY_REGISTRY)
    assert output.read_bytes() == registry.canonical_document_bytes() + b"\n"
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["registry_digest"].startswith("sha256:")


def test_duplicate_strategy_ids_fail() -> None:
    data = base_registry()
    data["strategies"].append(deepcopy(data["strategies"][0]))
    with pytest.raises(ValueError, match="duplicate strategy ids"):
        parse_registry(data)


def test_duplicate_json_object_keys_fail() -> None:
    with pytest.raises(ValueError, match="duplicate object key"):
        loads_registry('{"registry_schema_version":1,"registry_schema_version":1}')


def test_unknown_schema_version_and_implementation_fail() -> None:
    data = base_registry()
    data["registry_schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported registry schema version"):
        parse_registry(data)

    data = base_registry()
    data["strategies"][0]["implementation"] = "arbitrary.module.path"
    with pytest.raises(ValueError, match="implementation is unknown"):
        parse_registry(data)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_missing_and_extra_parameters_fail(change) -> None:
    data = base_registry()
    parameters = data["strategies"][0]["parameters"]
    if change == "missing":
        parameters.clear()
        match = "missing keys: weekday"
    else:
        parameters["secret_default"] = 1
        match = "unsupported keys: secret_default"
    with pytest.raises(ValueError, match=match):
        parse_registry(data)


@pytest.mark.parametrize(
    "value,match",
    [
        (True, "not a boolean"),
        ("NaN", "must be finite"),
        ("Infinity", "must be finite"),
        ("-0.1", "at least 0"),
        ("1.1", "at most 1"),
    ],
)
def test_invalid_allocation_fractions_fail(value, match) -> None:
    data = base_registry()
    data["strategies"][1]["parameters"]["allocation_fraction"] = value
    with pytest.raises(ValueError, match=match):
        parse_registry(data)


def test_json_nan_and_infinity_fail_before_validation() -> None:
    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="must not contain"):
            loads_registry(
                '{"registry_schema_version":1,"registry_id":"x","strategies":['
                '{"strategy_id":"x","implementation":"indicator_band_fixture",'
                '"strategy_version":"1","hypothesis":"x",'
                '"decision_cadence":"every_candle","required_indicators":['
                '{"name":"signal","definition_id":"fixture.signal","version":"1",'
                '"warmup_candles":0,"parameters":{}}],"parameters":{'
                f'"allocation_fraction":{value},"lower_threshold":0,"upper_threshold":1'
                '},"maximum_pending_cash_age_days":null,"minimum_order":{'
                '"amount":"0","behavior":"skip"},"research_status":"exploratory"}]}'
            )


def test_boolean_integer_and_invalid_weekday_fail() -> None:
    for value in (True, 7, -1, "0"):
        data = base_registry()
        data["strategies"][0]["parameters"]["weekday"] = value
        with pytest.raises(ValueError):
            parse_registry(data)


def test_impossible_threshold_ordering_fails() -> None:
    data = base_registry()
    data["strategies"][1]["parameters"]["lower_threshold"] = "2"
    with pytest.raises(ValueError, match="strictly lower"):
        parse_registry(data)


def test_unsupported_cadence_fails() -> None:
    data = base_registry()
    data["strategies"][0]["decision_cadence"] = "every_candle"
    with pytest.raises(ValueError, match="unsupported by fixed_weekly"):
        parse_registry(data)


def test_required_indicator_contract_is_exact() -> None:
    data = base_registry()
    data["strategies"][1]["required_indicators"] = []
    with pytest.raises(ValueError, match="exactly the indicators: signal"):
        parse_registry(data)


def test_invalid_pending_age_and_minimum_order_fail() -> None:
    data = base_registry()
    data["strategies"][0]["maximum_pending_cash_age_days"] = True
    with pytest.raises(ValueError, match="not a boolean"):
        parse_registry(data)

    data = base_registry()
    data["strategies"][0]["minimum_order"]["behavior"] = "round_up"
    with pytest.raises(ValueError, match="behavior must be one of"):
        parse_registry(data)


def test_provenance_is_complete_and_canonical() -> None:
    registry = parse_registry(base_registry())
    provenance = strategy_provenance(registry, "band-fixture", "A" * 40)

    assert provenance["registry_schema_version"] == 1
    assert provenance["registry_id"] == "research-registry"
    assert provenance["registry_digest"] == registry.digest
    assert provenance["strategy_id"] == "band-fixture"
    assert provenance["strategy_version"] == "2"
    assert provenance["implementation_identity"].endswith("indicator_band@1")
    assert provenance["parameters"]["allocation_fraction"] == "0.5"
    assert provenance["indicator_definitions"][0]["warmup_candles"] == 20
    assert provenance["repository_commit"] == "a" * 40
