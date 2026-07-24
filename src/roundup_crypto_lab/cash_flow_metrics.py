"""Shared cash-flow-aware performance and risk metrics."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

SCHEMA_VERSION = "cash-flow-metrics/v1"
SECONDS_PER_YEAR = Decimal("31557600")
XIRR_STATUSES = frozenset(
    {
        "converged",
        "undefined_no_negative_cash_flow",
        "undefined_no_positive_cash_flow",
        "not_converged",
    }
)


def _decimal(value: object, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be decimal") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{name} must be ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def maximum_drawdown(values: list[Decimal]) -> Decimal:
    """Return peak-to-trough drawdown for a non-empty positive series."""
    if not values or any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("drawdown values must be non-empty, finite, and positive")
    peak = values[0]
    maximum = Decimal("0")
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return maximum


def _xnpv(rate: float, cash_flows: list[tuple[datetime, Decimal]]) -> float:
    origin = cash_flows[0][0]
    base = 1.0 + rate
    if base <= 0:
        return math.inf
    total = 0.0
    for timestamp, amount in cash_flows:
        years = (timestamp - origin).total_seconds() / float(SECONDS_PER_YEAR)
        try:
            total += float(amount) / math.pow(base, years)
        except (OverflowError, ValueError):
            return math.copysign(math.inf, float(amount))
    return total


def dated_irr(cash_flows: list[tuple[datetime, Decimal]]) -> tuple[Decimal | None, str]:
    """Return deterministic annualized XIRR and an explicit convergence status."""
    if not cash_flows:
        return None, "undefined_no_negative_cash_flow"
    ordered = sorted(cash_flows, key=lambda row: row[0])
    if not any(amount < 0 for _, amount in ordered):
        return None, "undefined_no_negative_cash_flow"
    if not any(amount > 0 for _, amount in ordered):
        return None, "undefined_no_positive_cash_flow"
    at_zero = _xnpv(0.0, ordered)
    tolerance = 1e-12 * max(1.0, sum(abs(float(amount)) for _, amount in ordered))
    if math.isfinite(at_zero) and abs(at_zero) <= tolerance:
        return Decimal("0"), "converged"

    low = -0.999999999
    high = 1.0
    low_value = _xnpv(low, ordered)
    high_value = _xnpv(high, ordered)
    for _ in range(64):
        if math.isfinite(low_value) and math.isfinite(high_value) and low_value * high_value <= 0:
            break
        high = high * 2.0 + 1.0
        high_value = _xnpv(high, ordered)
    else:
        return None, "not_converged"

    for _ in range(256):
        midpoint = (low + high) / 2.0
        value = _xnpv(midpoint, ordered)
        if not math.isfinite(value):
            low = midpoint
            continue
        if abs(value) <= tolerance or high - low <= 1e-12:
            return Decimal(str(midpoint)), "converged"
        if low_value * value <= 0:
            high = midpoint
        else:
            low = midpoint
            low_value = value
    return None, "not_converged"


def _time_weighted_averages(
    snapshots: list[dict[str, object]], period_end: datetime
) -> tuple[Decimal, Decimal]:
    if not snapshots:
        raise ValueError("cash-flow metrics require a non-empty equity curve")
    ordered = sorted(snapshots, key=lambda row: _timestamp(row["timestamp"], "snapshot timestamp"))
    timestamps = [_timestamp(row["timestamp"], "snapshot timestamp") for row in ordered]
    if len(set(timestamps)) != len(timestamps) or timestamps != sorted(timestamps):
        raise ValueError("cash-flow metric snapshots must be strictly chronological")
    end = _timestamp(period_end, "period end")
    if timestamps[-1] >= end:
        raise ValueError("period end must be after the final snapshot")

    deployed_weighted = Decimal("0")
    utilization_weighted = Decimal("0")
    duration_total = Decimal("0")
    for index, row in enumerate(ordered):
        timestamp = timestamps[index]
        next_timestamp = timestamps[index + 1] if index + 1 < len(ordered) else end
        duration = Decimal(str((next_timestamp - timestamp).total_seconds()))
        if duration <= 0:
            raise ValueError("cash-flow metric snapshot duration must be positive")
        equity = _decimal(row["equity"], "snapshot equity")
        deployed = _decimal(row["asset_value"], "snapshot asset value")
        if equity < 0 or deployed < 0 or deployed > equity:
            raise ValueError("snapshot asset value must be within portfolio equity")
        deployed_weighted += deployed * duration
        utilization_weighted += (Decimal("0") if equity == 0 else deployed / equity) * duration
        duration_total += duration
    return deployed_weighted / duration_total, utilization_weighted / duration_total


def build_cash_flow_metrics(
    *,
    initial_capital: object,
    monthly_budget: object,
    fee_ratio: object,
    contributions: list[dict[str, object]],
    snapshots: list[dict[str, object]],
    total_fees: object,
    period_end: datetime,
) -> dict[str, object]:
    """Build the common metric schema from exact contribution and equity ledgers."""
    initial = _decimal(initial_capital, "initial capital")
    monthly = _decimal(monthly_budget, "monthly budget")
    fee = _decimal(fee_ratio, "fee ratio")
    fees = _decimal(total_fees, "total fees")
    if initial <= 0 or monthly <= 0 or fee < 0 or fee >= 1 or fees < 0:
        raise ValueError("invalid investment plan for cash-flow metrics")
    if not contributions or not snapshots:
        raise ValueError("cash-flow metrics require contributions and snapshots")

    normalized_contributions: list[tuple[datetime, Decimal]] = []
    for row in contributions:
        timestamp = _timestamp(row["timestamp"], "contribution timestamp")
        amount = _decimal(row["amount"], "contribution amount")
        if amount <= 0:
            raise ValueError("contribution amount must be positive")
        normalized_contributions.append((timestamp, amount))
    normalized_contributions.sort(key=lambda row: row[0])

    normalized_snapshots = sorted(
        snapshots, key=lambda row: _timestamp(row["timestamp"], "snapshot timestamp")
    )
    final = normalized_snapshots[-1]
    final_value = _decimal(final["equity"], "final value")
    final_cash = _decimal(final["cash"], "final cash")
    final_asset = _decimal(final["asset_value"], "final asset value")
    share_values = [_decimal(row["share_value"], "share value") for row in normalized_snapshots]
    equity_values = [_decimal(row["equity"], "portfolio value") for row in normalized_snapshots]
    if final_value != final_cash + final_asset:
        raise ValueError("final value must equal cash plus asset value")

    total_contributions = sum((amount for _, amount in normalized_contributions), Decimal("0"))
    profit_abs = final_value - total_contributions
    simple_return = profit_abs / total_contributions
    terminal_liquidation_value = final_cash + final_asset * (Decimal("1") - fee)
    money_weighted_return, money_weighted_status = dated_irr(
        [(timestamp, -amount) for timestamp, amount in normalized_contributions]
        + [(_timestamp(final["timestamp"], "final timestamp"), terminal_liquidation_value)]
    )
    average_deployed, utilization = _time_weighted_averages(normalized_snapshots, period_end)
    twr = share_values[-1] - Decimal("1")
    twr_drawdown = maximum_drawdown(share_values)
    raw_drawdown = maximum_drawdown(equity_values)

    def exact(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    return {
        "schema_version": SCHEMA_VERSION,
        "cash_flow_timing": "contributions_at_actual_utc_timestamp_before_same-candle_execution",
        "xirr_terminal_value_basis": "final_cash_plus_asset_value_net_of_one_exit_fee",
        "initial_capital": exact(initial),
        "monthly_budget": exact(monthly),
        "total_contributions": exact(total_contributions),
        "total_fees": exact(fees),
        "final_value": exact(final_value),
        "final_cash": exact(final_cash),
        "final_asset_value": exact(final_asset),
        "terminal_liquidation_value": exact(terminal_liquidation_value),
        "profit_abs": exact(profit_abs),
        "simple_return_on_contributions": exact(simple_return),
        "time_weighted_return": exact(twr),
        "money_weighted_return": exact(money_weighted_return),
        "money_weighted_return_status": money_weighted_status,
        "max_drawdown_time_weighted": exact(twr_drawdown),
        "max_drawdown_raw_portfolio": exact(raw_drawdown),
        "average_capital_deployed": exact(average_deployed),
        "capital_utilization_ratio": exact(utilization),
    }
