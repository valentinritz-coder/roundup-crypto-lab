"""Cost-profile-aware execution engine for the frozen short-delay DCA rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from roundup_crypto_lab.deployment_engine import INTERVAL, build_result
from roundup_crypto_lab.execution_costs import (
    ExecutionCostProfile,
    enrich_result_with_execution_costs,
    execute_costed_purchase,
)
from roundup_crypto_lab.investment_plan import CashFlowEvent
from roundup_crypto_lab.short_delay_dca import MAXIMUM_DELAY_DAYS, SUPPORTED_STRATEGIES

FOUR_HOUR_START_HOURS = (0, 4, 8, 12, 16, 20)


@dataclass(frozen=True)
class CompletedDailyClose:
    """UTC daily close with the exact timestamp at which it became visible."""

    day: date
    close: Decimal
    visible_at: datetime


@dataclass(frozen=True)
class DelayDecision:
    """One causal decision made for one isolated contribution bucket."""

    decision_at: datetime
    visible_data_cutoff: datetime
    latest_observation_day: date
    latest_close: Decimal
    action: str
    reason: str
    pending_cash: Decimal


def _as_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def completed_daily_closes(candles: pd.DataFrame) -> tuple[CompletedDailyClose, ...]:
    """Aggregate complete UTC days from six exact Kraken 4h candle starts."""

    required = {"date", "close"}
    if candles.empty or not required.issubset(candles.columns):
        raise ValueError("short-delay execution requires non-empty date and close columns")
    rows: dict[datetime, Decimal] = {}
    for row in candles.loc[:, ["date", "close"]].itertuples(index=False):
        timestamp = _as_utc(
            pd.Timestamp(row.date).to_pydatetime(),
            "candle timestamp",
        )
        close = Decimal(str(row.close))
        if not close.is_finite() or close <= 0:
            raise ValueError("candle closes must be finite and positive")
        if timestamp in rows:
            raise ValueError("candle timestamps must be unique")
        rows[timestamp] = close

    observations: list[CompletedDailyClose] = []
    first_day = min(rows).date()
    last_day = max(rows).date()
    current = first_day
    while current <= last_day:
        starts = [
            datetime(
                current.year,
                current.month,
                current.day,
                hour,
                tzinfo=UTC,
            )
            for hour in FOUR_HOUR_START_HOURS
        ]
        present = [timestamp in rows for timestamp in starts]
        if any(present) and not all(present):
            raise ValueError(
                f"incomplete UTC daily observation for {current.isoformat()}"
            )
        if all(present):
            observations.append(
                CompletedDailyClose(
                    day=current,
                    close=rows[starts[-1]],
                    visible_at=starts[-1] + INTERVAL,
                )
            )
        current += timedelta(days=1)
    return tuple(observations)


def _visible_series(
    observations: Sequence[CompletedDailyClose],
    decision_at: datetime,
) -> tuple[dict[date, Decimal], CompletedDailyClose]:
    cutoff = _as_utc(decision_at, "decision timestamp")
    visible = [
        observation
        for observation in observations
        if observation.visible_at <= cutoff
    ]
    if not visible:
        raise ValueError(
            f"no completed daily observation visible at {cutoff.isoformat()}"
        )
    by_day = {observation.day: observation.close for observation in visible}
    if len(by_day) != len(visible):
        raise ValueError("completed daily observations must have unique dates")
    return by_day, visible[-1]


def _window(
    closes: Mapping[date, Decimal],
    end_day: date,
    length: int,
) -> tuple[Decimal, ...]:
    offsets = range(length - 1, -1, -1)
    days = tuple(end_day - timedelta(days=offset) for offset in offsets)
    missing = [day for day in days if day not in closes]
    if missing:
        missing_text = ", ".join(day.isoformat() for day in missing)
        raise ValueError(
            "missing completed daily observation required by signal: "
            f"{missing_text}"
        )
    return tuple(closes[day] for day in days)


def _sma(
    closes: Mapping[date, Decimal],
    end_day: date,
    length: int = 7,
) -> Decimal:
    values = _window(closes, end_day, length)
    return sum(values, Decimal("0")) / Decimal(length)


def _delay_condition(
    strategy_id: str,
    closes: Mapping[date, Decimal],
    latest_day: date,
) -> tuple[bool, str]:
    latest = closes[latest_day]
    if strategy_id == "negative_7d_return_delay":
        prior_day = latest_day - timedelta(days=7)
        prior = _window(closes, prior_day, 1)[0]
        return latest < prior, "negative_7d_return"
    if strategy_id == "below_7d_sma_delay":
        average = _sma(closes, latest_day - timedelta(days=1))
        return latest < average, "below_previous_7d_sma"
    if strategy_id == "confirmed_short_decline_delay":
        current_average = _sma(closes, latest_day)
        prior_average = _sma(closes, latest_day - timedelta(days=3))
        delayed = latest < current_average and current_average < prior_average
        return delayed, "below_7d_sma_with_falling_sma"
    if strategy_id == "monthly_dca_control":
        return False, "monthly_dca_control"
    raise ValueError(f"unsupported strategy_id: {strategy_id}")


def _release_condition(
    strategy_id: str,
    closes: Mapping[date, Decimal],
    latest_day: date,
) -> tuple[bool, str]:
    if strategy_id == "confirmed_short_decline_delay":
        previous_day = latest_day - timedelta(days=1)
        previous = _window(closes, previous_day, 1)[0]
        return closes[latest_day] > previous, "first_positive_daily_close"
    delayed, reason = _delay_condition(strategy_id, closes, latest_day)
    return not delayed, f"signal_cleared:{reason}"


def _decision_row(
    *,
    decision_at: datetime,
    latest: CompletedDailyClose,
    action: str,
    reason: str,
    pending_cash: Decimal,
) -> DelayDecision:
    return DelayDecision(
        decision_at=decision_at,
        visible_data_cutoff=decision_at,
        latest_observation_day=latest.day,
        latest_close=latest.close,
        action=action,
        reason=reason,
        pending_cash=pending_cash,
    )


def _execution_timestamp(
    strategy_id: str,
    event: CashFlowEvent,
    observations: Sequence[CompletedDailyClose],
) -> tuple[datetime, str, tuple[DelayDecision, ...]]:
    contributed_at = _as_utc(event.contributed_at, "contribution timestamp")
    if strategy_id == "monthly_dca_control" or event.kind != "monthly":
        return contributed_at, "control_immediate", ()

    closes, latest = _visible_series(observations, contributed_at)
    delayed, delay_reason = _delay_condition(strategy_id, closes, latest.day)
    first = _decision_row(
        decision_at=contributed_at,
        latest=latest,
        action="delay" if delayed else "execute",
        reason=delay_reason if delayed else f"signal_clear:{delay_reason}",
        pending_cash=event.amount if delayed else Decimal("0"),
    )
    ledger = [first]
    if not delayed:
        return contributed_at, "immediate", tuple(ledger)

    for elapsed in range(1, MAXIMUM_DELAY_DAYS + 1):
        decision_at = contributed_at + timedelta(days=elapsed)
        closes, latest = _visible_series(observations, decision_at)
        if elapsed == MAXIMUM_DELAY_DAYS:
            ledger.append(
                _decision_row(
                    decision_at=decision_at,
                    latest=latest,
                    action="execute",
                    reason="forced_day_7",
                    pending_cash=Decimal("0"),
                )
            )
            return decision_at, "forced", tuple(ledger)
        release, release_reason = _release_condition(
            strategy_id,
            closes,
            latest.day,
        )
        ledger.append(
            _decision_row(
                decision_at=decision_at,
                latest=latest,
                action="execute" if release else "wait",
                reason=release_reason,
                pending_cash=Decimal("0") if release else event.amount,
            )
        )
        if release:
            return decision_at, "signal_release", tuple(ledger)
    raise AssertionError("unreachable")


def _ledger_artifact(
    contribution_id: str,
    rows: Sequence[DelayDecision],
) -> list[dict[str, Any]]:
    return [
        {
            "contribution_id": contribution_id,
            "decision_at": row.decision_at.isoformat(),
            "visible_data_cutoff": row.visible_data_cutoff.isoformat(),
            "latest_observation_day": row.latest_observation_day.isoformat(),
            "latest_close": row.latest_close,
            "action": row.action,
            "reason": row.reason,
            "pending_cash": row.pending_cash,
        }
        for row in rows
    ]


def execute_short_delay_strategy(
    *,
    strategy_id: str,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    pair: str,
    profile: ExecutionCostProfile,
) -> dict[str, Any]:
    """Execute one frozen strategy with isolated contribution provenance."""

    if strategy_id not in SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported strategy_id: {strategy_id}")
    if not isinstance(profile, ExecutionCostProfile):
        raise TypeError("profile must be an ExecutionCostProfile")
    if not events:
        raise ValueError("short-delay execution requires contribution events")
    observations = completed_daily_closes(candles)
    ordered_events = tuple(
        sorted(events, key=lambda item: (item.contributed_at, item.kind))
    )
    purchases: list[dict[str, Any]] = []
    signal_ledger: list[dict[str, Any]] = []
    allocations: list[dict[str, Any]] = []
    impacts: list[dict[str, Any]] = []

    for index, event in enumerate(ordered_events):
        if event.amount <= 0:
            raise ValueError("contribution amounts must be positive")
        contribution_id = f"contribution-{index:06d}"
        scheduled_at, release_type, decisions = _execution_timestamp(
            strategy_id,
            event,
            observations,
        )
        execution = execute_costed_purchase(
            candles,
            event,
            scheduled_at,
            event.amount,
            profile,
        )
        baseline = execute_costed_purchase(
            candles,
            event,
            _as_utc(event.contributed_at, "contribution timestamp"),
            event.amount,
            profile,
        )
        if execution is None or baseline is None:
            raise ValueError("insufficient candle coverage for contribution execution")
        executed_at = datetime.fromisoformat(execution["executed_at"]).astimezone(UTC)
        contributed_at = _as_utc(event.contributed_at, "contribution timestamp")
        waiting = executed_at - contributed_at
        maximum_wait = timedelta(days=MAXIMUM_DELAY_DAYS)
        if waiting > maximum_wait:
            raise ValueError(
                "contribution remained pending beyond the bounded execution window"
            )
        execution["decision_tag"] = f"short_delay.{strategy_id}"
        execution["order_tag"] = release_type
        execution["contribution_id"] = contribution_id
        execution["release_type"] = release_type
        execution["waiting_seconds"] = int(waiting.total_seconds())
        execution["funding_allocations"] = [
            {
                "contributed_at": contributed_at.isoformat(),
                "amount": event.amount,
            }
        ]
        purchases.append(execution)
        signal_ledger.extend(_ledger_artifact(contribution_id, decisions))
        allocations.append(
            {
                "contribution_id": contribution_id,
                "contributed_at": contributed_at.isoformat(),
                "funded_amount": event.amount,
                "scheduled_at": scheduled_at.isoformat(),
                "executed_at": execution["executed_at"],
                "release_type": release_type,
                "waiting_seconds": int(waiting.total_seconds()),
                "execution_price": execution["execution_price"],
                "explicit_fees": execution["fee_paid"],
                "estimated_spread_cost": execution["estimated_spread_cost"],
                "btc_quantity": execution["quantity"],
            }
        )
        strategy_price = execution["execution_price"]
        control_price = baseline["execution_price"]
        impacts.append(
            {
                "contribution_id": contribution_id,
                "monthly_dca_execution_price": control_price,
                "strategy_execution_price": strategy_price,
                "price_difference": strategy_price - control_price,
                "price_difference_ratio": (
                    strategy_price / control_price - Decimal("1")
                ),
            }
        )

    result = build_result(strategy_id, pair, candles, ordered_events, purchases)
    enrich_result_with_execution_costs(result, purchases, profile)
    delays = [
        Decimal(str(row["waiting_seconds"])) / Decimal("86400")
        for row in allocations
    ]
    delayed = [row for row in allocations if row["waiting_seconds"] > 0]
    result["strategy"] = {
        "strategy_id": strategy_id,
        "implementation": "short_delay_execution",
        "maximum_delay_calendar_days": MAXIMUM_DELAY_DAYS,
    }
    result["signal_ledger"] = signal_ledger
    result["funding_allocations"] = allocations
    result["price_impact_vs_monthly_dca"] = impacts
    result["delay_diagnostics"] = {
        "contribution_count": len(allocations),
        "delayed_contribution_count": len(delayed),
        "immediate_investment_rate": (
            Decimal(len(allocations) - len(delayed)) / Decimal(len(allocations))
        ),
        "average_delay_days": (
            sum(delays, Decimal("0")) / Decimal(len(delays))
        ),
        "maximum_delay_days": max(delays, default=Decimal("0")),
        "forced_deployment_count": sum(
            row["release_type"] == "forced" for row in allocations
        ),
        "final_pending_cash": Decimal("0"),
    }
    return result
