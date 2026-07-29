from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.short_delay_dca import (
    DailyObservation,
    completed_utc_daily_observations,
    decide_deployment,
    load_and_validate_protocol,
)


def observations(values: dict[date, str]) -> tuple[DailyObservation, ...]:
    return tuple(DailyObservation(day, Decimal(close)) for day, close in sorted(values.items()))


def declining_series(start: date, days: int = 30) -> tuple[DailyObservation, ...]:
    return observations(
        {start + timedelta(days=index): str(200 - index) for index in range(days)}
    )


def test_protocol_is_valid_and_contains_unchanged_control() -> None:
    protocol = load_and_validate_protocol()
    control = protocol["strategies"][0]
    assert control == {
        "strategy_id": "monthly_dca_control",
        "kind": "control",
        "parameters": {},
        "entry_rule": "deploy_immediately",
    }


def test_protocol_rejects_rule_drift(tmp_path: Path) -> None:
    payload = load_and_validate_protocol()
    payload["strategies"][1]["parameters"]["lookback_days"] = 8
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported parameters"):
        load_and_validate_protocol(path)


def test_current_incomplete_daily_candle_is_not_visible() -> None:
    day = date(2026, 7, 1)
    candles = []
    for hour in (4, 8, 12, 16, 20):
        candles.append(
            (
                datetime(2026, 7, 1, hour, tzinfo=timezone.utc),
                Decimal(str(hour)),
            )
        )
    candles.append((datetime(2026, 7, 2, 0, tzinfo=timezone.utc), Decimal("99")))

    before_close = completed_utc_daily_observations(
        candles, datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc)
    )
    at_close = completed_utc_daily_observations(
        candles, datetime(2026, 7, 2, 0, tzinfo=timezone.utc)
    )

    assert before_close == ()
    assert at_close == (DailyObservation(day, Decimal("99")),)


def test_daily_observation_requires_all_six_completed_4h_candles() -> None:
    candles = [
        (datetime(2026, 7, 1, hour, tzinfo=timezone.utc), Decimal("100"))
        for hour in (4, 8, 12, 20)
    ]
    candles.append((datetime(2026, 7, 2, 0, tzinfo=timezone.utc), Decimal("90")))
    assert completed_utc_daily_observations(
        candles, datetime(2026, 7, 2, 0, tzinfo=timezone.utc)
    ) == ()


def test_monthly_control_deploys_immediately() -> None:
    contribution = date(2026, 7, 1)
    decision = decide_deployment("monthly_dca_control", contribution, ())
    assert decision.deployment_day == contribution
    assert decision.delay_days == 0


def test_negative_return_deploys_immediately_when_signal_is_clear() -> None:
    contribution = date(2026, 7, 10)
    data = observations(
        {contribution - timedelta(days=offset): str(100 - offset) for offset in range(1, 9)}
    )
    decision = decide_deployment("negative_7d_return_delay", contribution, data)
    assert decision.deployment_day == contribution
    assert decision.reason == "signal_clear"


def test_negative_return_forces_full_deployment_after_exactly_seven_days() -> None:
    contribution = date(2026, 7, 20)
    data = declining_series(date(2026, 7, 1), 40)
    decision = decide_deployment("negative_7d_return_delay", contribution, data)
    assert decision.deployment_day == date(2026, 7, 27)
    assert decision.delay_days == 7
    assert decision.reason == "forced_deployment"


def test_below_sma_re_evaluates_and_deploys_when_signal_clears() -> None:
    contribution = date(2026, 7, 10)
    values = {
        contribution - timedelta(days=offset): "100" for offset in range(2, 10)
    }
    values[contribution - timedelta(days=1)] = "90"
    values[contribution] = "110"
    decision = decide_deployment(
        "below_7d_sma_delay", contribution, observations(values)
    )
    assert decision.deployment_day == contribution + timedelta(days=1)
    assert decision.reason == "signal_clear"


def test_confirmed_decline_deploys_on_first_completed_positive_close() -> None:
    contribution = date(2026, 7, 20)
    values = {
        date(2026, 7, 1) + timedelta(days=index): str(200 - index)
        for index in range(19)
    }
    values[date(2026, 7, 19)] = "180"
    values[date(2026, 7, 20)] = "181"
    decision = decide_deployment(
        "confirmed_short_decline_delay", contribution, observations(values)
    )
    assert decision.deployment_day == date(2026, 7, 21)
    assert decision.reason == "positive_close"


def test_missing_required_observation_fails_safe_to_immediate_deployment() -> None:
    contribution = date(2026, 7, 10)
    data = observations({contribution - timedelta(days=1): "90"})
    decision = decide_deployment("negative_7d_return_delay", contribution, data)
    assert decision.deployment_day == contribution
    assert decision.reason == "missing_observation"


def test_missing_observation_while_waiting_deploys_that_day() -> None:
    contribution = date(2026, 7, 20)
    data = list(declining_series(date(2026, 7, 1), 19))
    decision = decide_deployment("negative_7d_return_delay", contribution, tuple(data))
    assert decision.deployment_day == contribution + timedelta(days=1)
    assert decision.reason == "missing_observation"


def test_month_boundary_uses_calendar_days_without_carrying_reserve() -> None:
    contribution = date(2026, 1, 28)
    data = declining_series(date(2026, 1, 1), 50)
    decision = decide_deployment("negative_7d_return_delay", contribution, data)
    assert decision.deployment_day == date(2026, 2, 4)
    assert decision.delay_days == 7


def test_weekends_are_ordinary_calendar_days() -> None:
    contribution = date(2026, 7, 24)  # Friday
    data = declining_series(date(2026, 7, 1), 50)
    decision = decide_deployment("negative_7d_return_delay", contribution, data)
    assert decision.deployment_day == date(2026, 7, 31)
    assert decision.delay_days == 7
