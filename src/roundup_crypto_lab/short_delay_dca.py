"""Frozen short-delay DCA protocol and deterministic no-lookahead decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

PROTOCOL_PATH = Path("research/short_delay_dca_protocol.v1.json")
MAXIMUM_DELAY_DAYS = 7
SUPPORTED_STRATEGIES = {
    "monthly_dca_control": {},
    "negative_7d_return_delay": {
        "lookback_days": 7,
        "maximum_delay_calendar_days": 7,
    },
    "below_7d_sma_delay": {
        "moving_average_days": 7,
        "maximum_delay_calendar_days": 7,
    },
    "confirmed_short_decline_delay": {
        "moving_average_days": 7,
        "moving_average_slope_lookback_days": 3,
        "maximum_delay_calendar_days": 7,
    },
}


@dataclass(frozen=True)
class DailyObservation:
    """One completed UTC daily close derived from six completed 4h candles."""

    day: date
    close: Decimal


@dataclass(frozen=True)
class ContributionDecision:
    """Deterministic deployment result for one contribution."""

    contribution_day: date
    deployment_day: date
    reason: str

    @property
    def delay_days(self) -> int:
        return (self.deployment_day - self.contribution_day).days


def _strict_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        raise ValueError(f"{name} is missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{name} has unsupported keys: {', '.join(sorted(extra))}")


def load_and_validate_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    """Load the versioned protocol and reject parameter or rule drift."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    _strict_keys(
        payload,
        {
            "protocol_schema_version",
            "protocol_id",
            "market",
            "source_timeframe",
            "daily_timezone",
            "contribution_policy",
            "observation_contract",
            "strategies",
        },
        "protocol",
    )
    if payload["protocol_schema_version"] != 1:
        raise ValueError("protocol_schema_version must be 1")
    if payload["protocol_id"] != "short_delay_dca_btc_eur_v1":
        raise ValueError("unsupported protocol_id")
    if (payload["market"], payload["source_timeframe"], payload["daily_timezone"]) != (
        "BTC/EUR",
        "4h",
        "UTC",
    ):
        raise ValueError("market data contract drifted")

    contribution = payload["contribution_policy"]
    _strict_keys(
        contribution,
        {
            "source",
            "same_dates_and_amounts",
            "maximum_delay_calendar_days",
            "full_deployment_required",
            "reserve_may_cross_contribution_cycle",
            "selling_allowed",
            "borrowing_allowed",
            "future_contributions_may_be_anticipated",
        },
        "contribution_policy",
    )
    expected_contribution = {
        "source": "MonthlyDCA",
        "same_dates_and_amounts": True,
        "maximum_delay_calendar_days": 7,
        "full_deployment_required": True,
        "reserve_may_cross_contribution_cycle": False,
        "selling_allowed": False,
        "borrowing_allowed": False,
        "future_contributions_may_be_anticipated": False,
    }
    if contribution != expected_contribution:
        raise ValueError("contribution policy drifted")

    observation = payload["observation_contract"]
    _strict_keys(
        observation,
        {
            "daily_close_definition",
            "decision_timestamp",
            "latest_visible_daily_observation",
            "completed_candles_only",
            "missing_observation_policy",
            "weekend_policy",
            "month_boundary_policy",
        },
        "observation_contract",
    )
    if observation["completed_candles_only"] is not True:
        raise ValueError("only completed candles may be visible")
    if observation["missing_observation_policy"] != "deploy_immediately":
        raise ValueError("missing observations must fail safe to immediate deployment")

    strategies = payload["strategies"]
    if not isinstance(strategies, list) or len(strategies) != 4:
        raise ValueError("protocol must contain one control and exactly three candidates")
    seen: set[str] = set()
    for index, strategy in enumerate(strategies):
        _strict_keys(strategy, {"strategy_id", "kind", "parameters", "entry_rule"}, f"strategy[{index}]")
        strategy_id = strategy["strategy_id"]
        if strategy_id in seen or strategy_id not in SUPPORTED_STRATEGIES:
            raise ValueError(f"unsupported or duplicate strategy_id: {strategy_id}")
        seen.add(strategy_id)
        expected_kind = "control" if strategy_id == "monthly_dca_control" else "candidate"
        if strategy["kind"] != expected_kind:
            raise ValueError(f"invalid kind for {strategy_id}")
        if strategy["parameters"] != SUPPORTED_STRATEGIES[strategy_id]:
            raise ValueError(f"unsupported parameters for {strategy_id}")
        if not isinstance(strategy["entry_rule"], str) or not strategy["entry_rule"]:
            raise ValueError(f"entry_rule is required for {strategy_id}")
    if seen != set(SUPPORTED_STRATEGIES):
        raise ValueError("strategy set drifted")
    return payload


def completed_utc_daily_observations(
    candles: Sequence[tuple[datetime, Decimal]], decision_timestamp: datetime
) -> tuple[DailyObservation, ...]:
    """Convert 4h closes to completed UTC days visible at a decision timestamp.

    Candle timestamps are their UTC closing timestamps. A day D is complete only when the
    candle closing at D+1 00:00 UTC exists and is not later than the decision timestamp.
    """

    if decision_timestamp.tzinfo is None:
        raise ValueError("decision_timestamp must be timezone-aware")
    decision_timestamp = decision_timestamp.astimezone(timezone.utc)
    by_close: dict[datetime, Decimal] = {}
    for timestamp, close in candles:
        if timestamp.tzinfo is None:
            raise ValueError("candle timestamps must be timezone-aware")
        timestamp = timestamp.astimezone(timezone.utc)
        if timestamp <= decision_timestamp:
            by_close[timestamp] = Decimal(close)

    observations: list[DailyObservation] = []
    for timestamp in sorted(by_close):
        if timestamp.time() != time(0, 0):
            continue
        day = timestamp.date() - timedelta(days=1)
        required = [timestamp - timedelta(hours=offset) for offset in (20, 16, 12, 8, 4, 0)]
        if all(point in by_close for point in required):
            observations.append(DailyObservation(day=day, close=by_close[timestamp]))
    return tuple(observations)


def _series(observations: Sequence[DailyObservation]) -> dict[date, Decimal]:
    result = {item.day: item.close for item in observations}
    if len(result) != len(observations):
        raise ValueError("daily observations must contain unique dates")
    return result


def _window(closes: Mapping[date, Decimal], end_day: date, days: int) -> list[Decimal] | None:
    dates = [end_day - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    if any(day not in closes for day in dates):
        return None
    return [closes[day] for day in dates]


def _sma(closes: Mapping[date, Decimal], end_day: date, days: int = 7) -> Decimal | None:
    values = _window(closes, end_day, days)
    return None if values is None else sum(values, Decimal("0")) / Decimal(days)


def _should_delay(strategy_id: str, decision_day: date, closes: Mapping[date, Decimal]) -> bool | None:
    latest_day = decision_day - timedelta(days=1)
    latest = closes.get(latest_day)
    if latest is None:
        return None
    if strategy_id == "negative_7d_return_delay":
        prior = closes.get(latest_day - timedelta(days=7))
        return None if prior is None else latest < prior
    if strategy_id == "below_7d_sma_delay":
        previous_seven = _window(closes, latest_day - timedelta(days=1), 7)
        if previous_seven is None:
            return None
        return latest < sum(previous_seven, Decimal("0")) / Decimal(7)
    if strategy_id == "confirmed_short_decline_delay":
        current_sma = _sma(closes, latest_day, 7)
        earlier_sma = _sma(closes, latest_day - timedelta(days=3), 7)
        if current_sma is None or earlier_sma is None:
            return None
        return latest < current_sma and current_sma < earlier_sma
    if strategy_id == "monthly_dca_control":
        return False
    raise ValueError(f"unsupported strategy_id: {strategy_id}")


def decide_deployment(
    strategy_id: str,
    contribution_day: date,
    observations: Sequence[DailyObservation],
) -> ContributionDecision:
    """Return the deployment day using only observations completed before each decision."""

    if strategy_id not in SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported strategy_id: {strategy_id}")
    if strategy_id == "monthly_dca_control":
        return ContributionDecision(contribution_day, contribution_day, "control_immediate")

    closes = _series(observations)
    initial = _should_delay(strategy_id, contribution_day, closes)
    if initial is None:
        return ContributionDecision(contribution_day, contribution_day, "missing_observation")
    if not initial:
        return ContributionDecision(contribution_day, contribution_day, "signal_clear")

    for elapsed in range(1, MAXIMUM_DELAY_DAYS + 1):
        decision_day = contribution_day + timedelta(days=elapsed)
        if elapsed == MAXIMUM_DELAY_DAYS:
            return ContributionDecision(contribution_day, decision_day, "forced_deployment")
        latest_day = decision_day - timedelta(days=1)
        latest = closes.get(latest_day)
        previous = closes.get(latest_day - timedelta(days=1))
        if latest is None:
            return ContributionDecision(contribution_day, decision_day, "missing_observation")
        if strategy_id == "confirmed_short_decline_delay":
            if previous is None:
                return ContributionDecision(contribution_day, decision_day, "missing_observation")
            if latest > previous:
                return ContributionDecision(contribution_day, decision_day, "positive_close")
        else:
            signal = _should_delay(strategy_id, decision_day, closes)
            if signal is None:
                return ContributionDecision(contribution_day, decision_day, "missing_observation")
            if not signal:
                return ContributionDecision(contribution_day, decision_day, "signal_clear")

    raise AssertionError("unreachable")
