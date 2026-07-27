from __future__ import annotations

import json
from pathlib import Path

from roundup_crypto_lab.dca_robustness_orchestrator import (
    campaign_variants,
    phase_plan,
    write_variant_registry,
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
                "end": "20240101",
                "months": 24,
                "step_months": 12,
                "overlapping": True,
            },
            {
                "window_set_id": "holdout",
                "phase": "confirmation",
                "start": "20220101",
                "end": "20240101",
                "months": 24,
                "step_months": 24,
                "overlapping": False,
            },
        ],
        "parameter_neighborhoods": [
            {
                "family": "pilot",
                "strategy_id": "pilot",
                "variants": [
                    {"variant_id": "frozen-default", "overrides": {}},
                    {"variant_id": "adjacent", "overrides": {"threshold": "0.6"}},
                ],
            }
        ],
        "disclosures": [],
    }


def test_confirmation_plan_uses_only_frozen_default() -> None:
    rows = phase_plan(_campaign(), "confirmation")
    assert {row["phase"] for row in rows} == {"confirmation"}
    assert {row["variant_id"] for row in rows} == {"frozen-default"}


def test_exploratory_plan_materializes_neighborhoods() -> None:
    rows = phase_plan(_campaign(), "exploratory")
    assert {row["variant_id"] for row in rows} == {"frozen-default", "adjacent"}
    assert len(rows) == 6


def test_variant_registry_preserves_frozen_default(tmp_path: Path) -> None:
    registry = {
        "registry_id": "registry",
        "strategies": [{"strategy_id": "pilot", "parameters": {"threshold": "0.5"}}],
    }
    output = tmp_path / "variant.json"
    write_variant_registry(_campaign(), registry, "adjacent", output)
    payload = json.loads(output.read_text())
    assert registry["strategies"][0]["parameters"]["threshold"] == "0.5"
    assert payload["strategies"][0]["parameters"]["threshold"] == "0.6"
    assert payload["registry_id"].endswith("::adjacent")


def test_confirmation_variants_exclude_neighborhoods() -> None:
    assert [row["variant_id"] for row in campaign_variants(_campaign(), "confirmation")] == [
        "frozen-default"
    ]
