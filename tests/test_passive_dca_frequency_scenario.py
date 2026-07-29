from __future__ import annotations

import json
from pathlib import Path

from roundup_crypto_lab.dca_costed_execution import registered_frequency_strategies
from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.passive_dca_frequency_campaign import frequency_metadata
from roundup_crypto_lab.passive_dca_frequency_scenario import (
    _result_frequency,
    write_frequency_outputs,
)

REGISTRY_PATH = Path("config/passive-dca-frequency-strategies.json")


def _strategy_result(definition):
    metadata = frequency_metadata(definition)
    return {
        "benchmark": definition.strategy_id,
        "strategy": {
            "strategy_id": definition.strategy_id,
            "strategy_version": definition.strategy_version,
            "implementation": definition.implementation,
            "implementation_identity": definition.implementation_identity,
            "parameters": dict(definition.parameters),
        },
        "frequency": metadata,
        "purchase_ledger": [],
        "execution_costs": {
            "order_count": 1,
            "average_order_size": "40",
            "trading_fees_paid": "0",
            "fixed_order_fees_paid": "0",
            "explicit_fees_paid": "0",
            "estimated_spread_cost": "0",
            "total_execution_cost": "0",
        },
    }


def test_result_frequency_projects_all_committed_strategies() -> None:
    registry = load_registry(REGISTRY_PATH)
    definitions = registered_frequency_strategies(registry)
    projected = [
        _result_frequency({"strategy": _strategy_result(definition)["strategy"]})
        for definition in definitions
    ]
    assert [row["frequency"] for row in projected] == [
        "weekly",
        "monthly",
        "every-2-months",
        "every-2-months",
        "quarterly",
        "quarterly",
        "quarterly",
    ]
    assert [
        row["phase_offset_months"]
        for row in projected
        if row["frequency"] == "quarterly"
    ] == [0, 1, 2]


def test_frequency_outputs_encode_profile_frequency_and_phase(
    tmp_path: Path,
) -> None:
    registry = load_registry(REGISTRY_PATH)
    results = [
        _strategy_result(definition)
        for definition in registered_frequency_strategies(registry)
    ]
    comparison = [
        {
            "method": result["benchmark"],
            "strategy_id": result["strategy"]["strategy_id"],
            "final_value": "100",
            "final_crypto_quantity": "1",
            "final_uninvested_cash": "0",
            **result["execution_costs"],
            **result["frequency"],
        }
        for result in results
    ]
    payload = {
        "campaign": {
            "campaign_id": "passive-dca-frequency-test",
            "phase": "exploratory",
            "window_set_id": "window",
            "scenario_id": "scenario",
        },
        "scenario": {
            "pair": "BTC/EUR",
            "timeframe": "4h",
            "timerange": "20200101-20220101",
            "repository_commit": "a" * 40,
        },
        "registry": {
            "registry_id": registry.registry_id,
            "registry_digest": registry.digest,
        },
        "execution_cost_profile": {
            "cost_profile_id": "frictionless-control-v1",
            "profile_version": 1,
            "profile_digest": "sha256:test",
        },
        "results": results,
        "comparison": comparison,
    }
    write_frequency_outputs(payload, tmp_path)

    assert (
        tmp_path
        / "frequencies"
        / "weekly"
        / "phase-predefined-weekday"
        / "weekly-dca"
        / "manifest.json"
    ).is_file()
    quarterly_manifest = (
        tmp_path
        / "frequencies"
        / "quarterly"
        / "phase-2"
        / "quarterly-phase-2"
        / "manifest.json"
    )
    manifest = json.loads(quarterly_manifest.read_text(encoding="utf-8"))
    assert manifest["cost_profile_id"] == "frictionless-control-v1"
    assert manifest["frequency"]["phase_offset_months"] == 2

    root_manifest = json.loads(
        (tmp_path / "reproducibility-manifest.json").read_text(encoding="utf-8")
    )
    assert len(root_manifest["strategy_artifacts"]) == 7
    assert {
        row["frequency"]["frequency"]
        for row in root_manifest["strategy_artifacts"]
    } == {"weekly", "monthly", "every-2-months", "quarterly"}
