from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundup_crypto_lab import dca_campaign_scenario
from roundup_crypto_lab.dca_decimal_safety import exact_purchase


def _run(tmp_path: Path) -> str:
    registry = tmp_path / "registry.json"
    registry.write_text("{}\n", encoding="utf-8")
    return dca_campaign_scenario.run_campaign_scenario(
        data_dir=tmp_path / "data",
        pair="BTC/EUR",
        timeframe="4h",
        timerange="20180101-20200101",
        registry_path=registry,
        initial_capital="40",
        monthly_budget="40",
        contribution_day=1,
        fee="0.0026",
        repository_commit="deadbeef",
        output_dir=tmp_path / "result",
        window_set_id="rolling-24m",
        phase="exploratory",
        variant_id="frozen-default",
    )


def test_data_gap_is_recorded_as_an_explicit_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = (
        "critical 4h candle gap in BTC/EUR: largest gap 1 days 12:00:00 "
        "between 2018-01-11T20:00:00+00:00 and 2018-01-13T08:00:00+00:00"
    )

    def fail_comparison(**_: object) -> dict:
        raise ValueError(reason)

    monkeypatch.setattr(dca_campaign_scenario, "run_comparison", fail_comparison)

    assert _run(tmp_path) == "excluded"
    payload = json.loads(
        (tmp_path / "result" / "scenario-exclusion.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == "excluded"
    assert payload["scenario"] == {
        "pair": "BTC/EUR",
        "timeframe": "4h",
        "timerange": "20180101-20200101",
        "window_set_id": "rolling-24m",
        "phase": "exploratory",
        "variant_id": "frozen-default",
    }
    assert payload["exclusion"] == {
        "category": "input-data-quality",
        "reason": reason,
    }
    assert not (tmp_path / "result" / "controlled-comparison.json").exists()


def test_unexpected_comparison_error_still_fails_the_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "controlled comparison contains duplicate strategy identities"

    def fail_comparison(**_: object) -> dict:
        raise ValueError(message)

    monkeypatch.setattr(dca_campaign_scenario, "run_comparison", fail_comparison)

    with pytest.raises(ValueError, match="duplicate strategy identities"):
        _run(tmp_path)

    payload = json.loads(
        (tmp_path / "result" / "scenario-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == "failed"
    assert payload["failure"]["exception_type"] == "ValueError"
    assert payload["failure"]["message"] == message
    assert "fail_comparison" in payload["failure"]["traceback"]


def test_non_value_error_is_persisted_and_re_raised(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_comparison(**_: object) -> dict:
        raise RuntimeError("unexpected runtime failure")

    monkeypatch.setattr(dca_campaign_scenario, "run_comparison", fail_comparison)

    with pytest.raises(RuntimeError, match="unexpected runtime failure"):
        _run(tmp_path)

    payload = json.loads(
        (tmp_path / "result" / "scenario-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["failure"]["exception_type"] == "RuntimeError"
    assert payload["failure"]["message"] == "unexpected runtime failure"


def test_decimal_safety_installs_exact_purchase() -> None:
    dca_campaign_scenario._install_decimal_safety()

    assert dca_campaign_scenario.controlled_comparison.purchase is exact_purchase


def test_only_known_data_validation_failures_are_excludable() -> None:
    assert dca_campaign_scenario.is_excludable_data_error(
        ValueError("insufficient Kraken coverage at timerange start for BTC/EUR")
    )
    assert not dca_campaign_scenario.is_excludable_data_error(
        ValueError("pilot execution exceeds available contributed cash")
    )
