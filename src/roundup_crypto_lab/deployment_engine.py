"""Reusable exact-Decimal primitives for long-only passive capital deployment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.investment_plan import CashFlowEvent, InvestmentPlan

TIMEFRAME = "4h"
INTERVAL = timedelta(hours=4)
# A single absent 4h candle can defer a scheduled purchase to the next available
# candle. Longer interruptions cannot be treated as a normal delayed execution.
MAX_ALLOWED_GAP = timedelta(hours=8)

PURCHASE_LEDGER_FIELDS = [
    "contributed_at",
    "scheduled_at",
    "executed_at",
    "execution_price",
    "gross_contribution",
    "fee_paid",
    "net_contribution",
    "quantity",
    "cumulative_quantity",
    "cumulative_gross_contributions",
    "cumulative_fees",
    "residual_cash",
    "marked_to_market_portfolio_value",
]

WEEKDAYS = {
    name: number
    for number, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )
}


@dataclass(frozen=True)
class DeploymentBucket:
    """Aggregate cash available at one instant solely for purchase scheduling."""

    contributed_at: datetime
    amount: Decimal


def deployment_buckets(events: tuple[CashFlowEvent, ...]) -> tuple[DeploymentBucket, ...]:
    """Group same-instant cash flows without changing their investor-event records."""
    grouped: dict[datetime, Decimal] = {}
    for event in sorted(events, key=lambda item: (item.contributed_at, item.kind, item.amount)):
        grouped[event.contributed_at] = (
            grouped.get(event.contributed_at, Decimal("0")) + event.amount
        )
    return tuple(
        DeploymentBucket(contributed_at=timestamp, amount=amount)
        for timestamp, amount in sorted(grouped.items())
    )


def parse_timerange(value: str) -> tuple[datetime, datetime]:
    """Parse an end-exclusive UTC date range in the Freqtrade date-only syntax."""
    if len(value) != 17 or value[8] != "-" or not (value[:8] + value[9:]).isdigit():
        raise ValueError("timerange must use exactly YYYYMMDD-YYYYMMDD")
    try:
        start = datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=UTC)
        end = datetime.strptime(value[9:], "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("timerange contains an invalid calendar date") from exc
    if start >= end:
        raise ValueError("timerange start date must be strictly before end date")
    return start, end


def _data_file(data_dir: Path, pair: str, timeframe: str) -> Path:
    return data_dir / f"{pair.replace('/', '_')}-{timeframe}.feather"


def load_kraken_candles(data_dir: Path, pair: str, timeframe: str, timerange: str) -> pd.DataFrame:
    """Load and strictly validate existing Freqtrade Feather candles without downloading data."""
    if timeframe != TIMEFRAME:
        raise ValueError("only the prepared 4h timeframe is supported")
    path = _data_file(data_dir, pair, timeframe)
    if not path.is_file():
        raise ValueError(f"missing Kraken data for {pair}: {path}")
    frame = pd.read_feather(path)
    required = ["date", "open", "high", "low", "close", "volume"]
    if any(column not in frame for column in required) or frame.empty:
        raise ValueError(f"invalid OHLCV columns for {pair}")
    frame = frame[required].copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    if not frame["date"].is_monotonic_increasing or frame["date"].duplicated().any():
        raise ValueError(f"timestamps must be monotonic and unique for {pair}")
    numeric = frame[required[1:]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or (numeric[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"OHLC values must be finite and positive for {pair}")
    if (numeric["volume"] < 0).any():
        raise ValueError(f"volume must be finite and non-negative for {pair}")
    start, end = parse_timerange(timerange)
    selected = frame[(frame["date"] >= start) & (frame["date"] < end)].reset_index(drop=True)
    if selected.empty or selected.iloc[0]["date"].to_pydatetime() != start:
        raise ValueError(f"insufficient Kraken coverage at timerange start for {pair}")
    # A 4h candle beginning on the final date is outside this end-exclusive date timerange.
    if selected.iloc[-1]["date"].to_pydatetime() < end - INTERVAL:
        raise ValueError(f"insufficient Kraken coverage at timerange end for {pair}")
    gaps = selected["date"].diff().dropna()
    if (gaps > pd.Timedelta(MAX_ALLOWED_GAP)).any():
        largest_gap_index = gaps.idxmax()
        largest_gap = gaps.loc[largest_gap_index]
        before = selected.loc[largest_gap_index - 1, "date"].isoformat()
        after = selected.loc[largest_gap_index, "date"].isoformat()
        raise ValueError(
            f"critical 4h candle gap in {pair}: largest gap {largest_gap} "
            f"between {before} and {after}"
        )
    return selected


def candle_metadata(candles: pd.DataFrame, timerange: str) -> dict[str, int | float]:
    """Summarize timerange coverage without inferring or filling missing candles."""
    start, end = parse_timerange(timerange)
    expected_candles = int((end - start) / INTERVAL)
    gaps = candles["date"].diff().dropna()
    maximum_gap = gaps.max() if not gaps.empty else pd.Timedelta(0)
    return {
        "expected_candles": expected_candles,
        "actual_candles": len(candles),
        "missing_candles_estimate": max(0, expected_candles - len(candles)),
        "maximum_gap_hours": maximum_gap.total_seconds() / timedelta(hours=1).total_seconds(),
    }


def number(value: Decimal | None) -> float | None:
    """Project an exact Decimal to the legacy JSON-compatible numeric representation."""
    return None if value is None else float(value)


def _drawdown(values: list[Decimal]) -> Decimal:
    peak: Decimal | None = None
    maximum = Decimal("0")
    for value in values:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            maximum = max(maximum, (peak - value) / peak)
    return Decimal("0") if maximum < Decimal("1e-24") else maximum


def _purchase_at_or_after(candles: pd.DataFrame, scheduled: datetime) -> tuple[int, Any] | None:
    matching = candles.index[candles["date"] >= scheduled]
    if len(matching) == 0:
        return None
    index = int(matching[0])
    return index, candles.iloc[index]


def purchase(
    candles: pd.DataFrame,
    event: CashFlowEvent,
    scheduled_at: datetime,
    amount: Decimal,
    fee: Decimal,
) -> dict[str, Any] | None:
    """Execute a scheduled buy at the first eligible candle open."""
    matched = _purchase_at_or_after(candles, scheduled_at)
    if matched is None:
        return None
    index, candle = matched
    price = Decimal(str(candle["open"]))
    return {
        "contributed_at": event.contributed_at.isoformat(),
        "scheduled_at": scheduled_at.isoformat(),
        "executed_at": candle["date"].to_pydatetime().isoformat(),
        "execution_price": price,
        "gross_contribution": amount,
        "fee_paid": amount * fee,
        "net_contribution": amount * (Decimal("1") - fee),
        "quantity": amount * (Decimal("1") - fee) / price,
        "candle_index": index,
    }


def deployment_dates(
    start: datetime, end: datetime, method: str, weekly_day: int
) -> list[datetime]:
    """Build the existing daily or weekly deployment dates for one funding bucket."""
    dates: list[datetime] = []
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current < end:
        if method == "daily_dca" or (method == "weekly_dca" and current.weekday() == weekly_day):
            dates.append(current)
        current += timedelta(days=1)
    return dates


def deploy(
    plan: InvestmentPlan,
    events: tuple[CashFlowEvent, ...],
    candles: pd.DataFrame,
    method: str,
    weekly_day: int,
) -> list[dict[str, Any]]:
    """Deploy each cash flow only after it arrives; DCA splits it over its funding interval."""
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC) + INTERVAL
    purchases: list[dict[str, Any]] = []
    buckets = deployment_buckets(events)
    for position, bucket in enumerate(buckets):
        next_at = buckets[position + 1].contributed_at if position + 1 < len(buckets) else end
        bucket_event = CashFlowEvent(bucket.contributed_at, bucket.amount, "deployment")
        if method == "immediate":
            scheduled = [bucket.contributed_at]
        else:
            scheduled = deployment_dates(bucket.contributed_at, next_at, method, weekly_day)
        if not scheduled:
            continue
        portion = bucket.amount / len(scheduled)
        amounts = [portion] * (len(scheduled) - 1)
        amounts.append(bucket.amount - sum(amounts, Decimal("0")))
        for scheduled_at, amount in zip(scheduled, amounts, strict=True):
            executed = purchase(candles, bucket_event, scheduled_at, amount, plan.fee_ratio)
            if executed is not None:
                purchases.append(executed)
    return purchases


def validate_accounting_invariants(
    purchases: list[dict[str, Any]],
    *,
    quantity: Decimal,
    cash: Decimal,
    contributions: Decimal,
    invested: Decimal,
    fees: Decimal,
    final_price: Decimal,
    final_value: Decimal,
    expected_contributions: Decimal,
) -> None:
    """Independently recompute long-only ledger totals and fail closed on mismatch."""
    values = (quantity, cash, contributions, invested, fees, final_price, final_value)
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("passive accounting produced a non-finite or negative balance")

    running_quantity = running_fees = running_invested = Decimal("0")
    for executed in purchases:
        gross = executed["gross_contribution"]
        fee = executed["fee_paid"]
        net = executed["net_contribution"]
        execution_price = executed["execution_price"]
        acquired = executed["quantity"]
        if gross != fee + net or acquired != net / execution_price:
            raise ValueError("purchase ledger accounting invariant failed")
        running_quantity += acquired
        running_fees += fee
        running_invested += gross
        if executed["cumulative_quantity"] != running_quantity:
            raise ValueError("purchase ledger cumulative quantity invariant failed")
        if executed["cumulative_fees"] != running_fees:
            raise ValueError("purchase ledger cumulative fee invariant failed")

    if quantity != running_quantity or quantity != sum(
        (executed["net_contribution"] / executed["execution_price"] for executed in purchases),
        Decimal("0"),
    ):
        raise ValueError("final quantity does not equal independently recomputed ledger quantity")
    if invested != running_invested or fees != running_fees:
        raise ValueError("final invested capital or fees do not equal ledger totals")
    if purchases and purchases[-1]["cumulative_quantity"] != quantity:
        raise ValueError("final ledger cumulative quantity does not equal final quantity")
    if contributions != expected_contributions:
        raise ValueError("gross investor contributions do not equal the investment plan")
    # Decimal divisions used to split DCA buckets can retain a sub-atto residue.
    # Each executed purchase can contribute at most one unit of rounding tolerance,
    # while materially incorrect cash accounting still fails closed.
    cash_tolerance = Decimal("1e-24") * max(1, len(purchases))
    if abs(contributions - invested - cash) > cash_tolerance:
        raise ValueError("portfolio cash accounting invariant failed")
    if final_value != cash + quantity * final_price:
        raise ValueError("final portfolio valuation invariant failed")


def build_result(
    benchmark: str,
    pair: str,
    candles: pd.DataFrame,
    events: tuple[CashFlowEvent, ...],
    purchases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Account contributions, buys, valuations, equity and invariants deterministically."""
    purchases_by_index: dict[int, list[dict[str, Any]]] = {}
    for executed in purchases:
        purchases_by_index.setdefault(executed["candle_index"], []).append(executed)
    for pending in purchases_by_index.values():
        pending.sort(key=lambda row: (row["scheduled_at"], row["contributed_at"]))

    quantity = cash = contributions = invested = fees = Decimal("0")
    shares = Decimal("0")
    share_value = Decimal("1")
    event_index = 0
    equity: list[dict[str, Any]] = []
    for index, candle in candles.iterrows():
        timestamp = candle["date"].to_pydatetime()
        open_price = Decimal(str(candle["open"]))
        # Contributions are credited before buys. Existing holdings are marked at the
        # candle open solely to issue neutral performance shares for the cash flow.
        while event_index < len(events) and events[event_index].contributed_at <= timestamp:
            event = events[event_index]
            before_contribution = cash + quantity * open_price
            share_value = Decimal("1") if shares == 0 else before_contribution / shares
            shares += event.amount / share_value
            cash += event.amount
            contributions += event.amount
            event_index += 1
        executed_this_candle = purchases_by_index.get(index, [])
        for executed in executed_this_candle:
            if executed["gross_contribution"] - cash > Decimal("1e-24"):
                raise ValueError("purchase exceeds available investor cash")
            # The fee is taken from the gross order amount. It reduces acquired crypto,
            # not a separate cash balance; cash therefore falls by gross.
            cash = max(Decimal("0"), cash - executed["gross_contribution"])
            invested += executed["gross_contribution"]
            fees += executed["fee_paid"]
            quantity += executed["quantity"]
            executed["cumulative_quantity"] = quantity
            executed["cumulative_gross_contributions"] = contributions
            executed["cumulative_fees"] = fees
            executed["residual_cash"] = cash
        crypto_value = quantity * Decimal(str(candle["close"]))
        for executed in executed_this_candle:
            executed["marked_to_market_portfolio_value"] = cash + crypto_value
        portfolio_value = cash + crypto_value
        if shares:
            share_value = portfolio_value / shares
        equity.append(
            {
                "timestamp": timestamp.isoformat(),
                "cash_balance": cash,
                "crypto_value": crypto_value,
                "portfolio_value": portfolio_value,
                "net_value": portfolio_value - contributions,
                "cumulative_contributions": contributions,
                "capital_invested": invested,
                "cumulative_fees_paid": fees,
                "time_weighted_share_value": share_value,
            }
        )
    total_contributions = sum((event.amount for event in events), Decimal("0"))
    if contributions != total_contributions:
        raise ValueError("timerange candles did not credit every contribution")
    final_price = Decimal(str(candles.iloc[-1]["close"]))
    final_crypto_value = quantity * final_price
    final_value = cash + final_crypto_value
    average = invested / quantity if quantity else None
    validate_accounting_invariants(
        purchases,
        quantity=quantity,
        cash=cash,
        contributions=contributions,
        invested=invested,
        fees=fees,
        final_price=final_price,
        final_value=final_value,
        expected_contributions=total_contributions,
    )
    raw_drawdown = _drawdown([row["portfolio_value"] for row in equity])
    time_weighted_drawdown = _drawdown([row["time_weighted_share_value"] for row in equity])
    return {
        "benchmark": benchmark,
        "category": "benchmark",
        "pair": pair,
        "number_of_buys": len(purchases),
        "capital_invested": number(invested),
        "total_contributions": number(total_contributions),
        "cash_balance": number(cash),
        "cash_balance_exact": str(cash),
        "cash_available": number(cash),
        "fees_paid": number(fees),
        "quantity": number(quantity),
        "quantity_exact": str(quantity),
        "average_entry_price": number(average),
        "average_entry_price_exact": None if average is None else str(average),
        "final_price": number(final_price),
        "final_price_exact": str(final_price),
        "final_crypto_value": number(final_crypto_value),
        "final_value": number(final_value),
        "final_value_exact": str(final_value),
        "portfolio_value": number(final_value),
        "profit_total_abs": number(final_value - total_contributions),
        "profit_total": number((final_value - total_contributions) / total_contributions),
        "max_drawdown": number(raw_drawdown),
        "max_drawdown_raw_portfolio": number(raw_drawdown),
        "max_drawdown_time_weighted": number(time_weighted_drawdown),
        "profit_factor": None,
        "expectancy": None,
        "winrate": None,
        "equity_curve": [
            {
                key: (number(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
            }
            for row in equity
        ],
        "purchase_ledger": [
            {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
                if key != "candle_index"
            }
            for row in purchases
        ],
        "purchases": [
            {
                key: (number(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
                if key != "candle_index"
            }
            for row in purchases
        ],
    }
