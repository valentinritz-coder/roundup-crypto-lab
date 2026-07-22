"""Deterministic, long-only passive benchmarks on prepared Kraken OHLCV data.

Purchases use the open of the first candle at or after their scheduled UTC instant.
There is no sale: final values use the last eligible candle close and include no sale fee.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.investment_plan import (
    CashFlowEvent,
    InvestmentPlan,
    contribution_schedule,
)

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


WEEKDAYS = {
    name: number
    for number, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )
}


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


def _decimal(value: str | Decimal, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return result


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


def _candle_metadata(candles: pd.DataFrame, timerange: str) -> dict[str, int | float]:
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


def _number(value: Decimal | None) -> float | None:
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


def _assert_accounting_invariants(
    purchases: list[dict[str, Any]],
    *,
    quantity: Decimal,
    cash: Decimal,
    contributions: Decimal,
    invested: Decimal,
    fees: Decimal,
    final_price: Decimal,
    final_value: Decimal,
) -> None:
    """Fail closed if the long-only Decimal accounting identity is violated."""
    values = (quantity, cash, contributions, invested, fees, final_price, final_value)
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("passive accounting produced a non-finite or negative balance")
    previous_quantity = Decimal("0")
    for purchase in purchases:
        gross, fee, net, acquired = (
            purchase["gross_contribution"],
            purchase["fee_paid"],
            purchase["net_contribution"],
            purchase["quantity"],
        )
        if gross != fee + net or acquired != net / purchase["execution_price"]:
            raise ValueError("purchase ledger accounting invariant failed")
        if purchase["cumulative_quantity"] < previous_quantity:
            raise ValueError("passive long-only quantity decreased")
        previous_quantity = purchase["cumulative_quantity"]
    tolerance = Decimal("1e-24")
    if (
        abs(contributions - invested - cash) > tolerance
        or abs(final_value - cash - quantity * final_price) > tolerance
    ):
        raise ValueError("portfolio accounting invariant failed")


def _build_result(
    benchmark: str,
    pair: str,
    candles: pd.DataFrame,
    events: tuple[CashFlowEvent, ...],
    purchases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Account contributions, then buys, then each candle close deterministically."""
    purchases_by_index: dict[int, list[dict[str, Any]]] = {}
    for purchase in purchases:
        purchases_by_index.setdefault(purchase["candle_index"], []).append(purchase)
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
        # Contributions are credited before buys.  Existing holdings are marked at the
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
        for purchase in executed_this_candle:
            if purchase["gross_contribution"] - cash > Decimal("1e-24"):
                raise ValueError("purchase exceeds available investor cash")
            # The fee is taken from the gross order amount.  It reduces acquired
            # crypto, not a separate cash balance; cash therefore falls by gross.
            # Repeating Decimal divisions in DCA portions can leave a sub-atto
            # rounding residue, which is explicitly normalized to zero.
            cash = max(Decimal("0"), cash - purchase["gross_contribution"])
            invested += purchase["gross_contribution"]
            fees += purchase["fee_paid"]
            quantity += purchase["quantity"]
            purchase["cumulative_quantity"] = quantity
            purchase["cumulative_gross_contributions"] = contributions
            purchase["cumulative_fees"] = fees
            purchase["residual_cash"] = cash
        crypto_value = quantity * Decimal(str(candle["close"]))
        for purchase in executed_this_candle:
            purchase["marked_to_market_portfolio_value"] = cash + crypto_value
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
    _assert_accounting_invariants(
        purchases,
        quantity=quantity,
        cash=cash,
        contributions=contributions,
        invested=invested,
        fees=fees,
        final_price=final_price,
        final_value=final_value,
    )
    raw_drawdown = _drawdown([row["portfolio_value"] for row in equity])
    time_weighted_drawdown = _drawdown([row["time_weighted_share_value"] for row in equity])
    return {
        "benchmark": benchmark,
        "category": "benchmark",
        "pair": pair,
        "number_of_buys": len(purchases),
        "capital_invested": _number(invested),
        "total_contributions": _number(total_contributions),
        "cash_balance": _number(cash),
        "cash_balance_exact": str(cash),
        "cash_available": _number(cash),
        "fees_paid": _number(fees),
        "quantity": _number(quantity),
        "quantity_exact": str(quantity),
        "average_entry_price": _number(average),
        "average_entry_price_exact": None if average is None else str(average),
        "final_price": _number(final_price),
        "final_price_exact": str(final_price),
        "final_crypto_value": _number(final_crypto_value),
        "final_value": _number(final_value),
        "final_value_exact": str(final_value),
        "portfolio_value": _number(final_value),
        "profit_total_abs": _number(final_value - total_contributions),
        "profit_total": _number((final_value - total_contributions) / total_contributions),
        "max_drawdown": _number(raw_drawdown),
        "max_drawdown_raw_portfolio": _number(raw_drawdown),
        "max_drawdown_time_weighted": _number(time_weighted_drawdown),
        "profit_factor": None,
        "expectancy": None,
        "winrate": None,
        "equity_curve": [
            {
                key: (_number(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
            }
            for row in equity
        ],
        # ``purchase_ledger`` is deliberately duplicated under the legacy
        # ``purchases`` name until downstream report consumers migrate.
        "purchase_ledger": [
            {
                # Decimal strings retain every significant digit in the JSON and CSV
                # audit artifacts; the legacy ``purchases`` projection remains numeric.
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
                if key != "candle_index"
            }
            for row in purchases
        ],
        "purchases": [
            {
                key: (_number(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
                if key != "candle_index"
            }
            for row in purchases
        ],
    }


def buy_and_hold(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Removed legacy API; use ``run_passive_benchmarks`` with ``InvestmentPlan`` inputs."""
    raise ValueError("buy_and_hold is removed; use the shared InvestmentPlan benchmark runner")


def dca(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Removed legacy API that accepted an independent contribution amount."""
    raise ValueError("dca is removed; use --monthly-budget with the shared InvestmentPlan")


def _purchase(
    candles: pd.DataFrame,
    event: CashFlowEvent,
    scheduled_at: datetime,
    amount: Decimal,
    fee: Decimal,
) -> dict[str, Any] | None:
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


def _deployment_dates(
    start: datetime, end: datetime, method: str, weekly_day: int
) -> list[datetime]:
    dates: list[datetime] = []
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current < end:
        if method == "daily_dca" or (method == "weekly_dca" and current.weekday() == weekly_day):
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _deploy(
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
            scheduled = _deployment_dates(bucket.contributed_at, next_at, method, weekly_day)
        if not scheduled:
            continue
        portion = bucket.amount / len(scheduled)
        amounts = [portion] * (len(scheduled) - 1)
        amounts.append(bucket.amount - sum(amounts, Decimal("0")))
        for scheduled_at, amount in zip(scheduled, amounts, strict=True):
            purchase = _purchase(candles, bucket_event, scheduled_at, amount, plan.fee_ratio)
            if purchase is not None:
                purchases.append(purchase)
    return purchases


def run_passive_benchmarks(
    data_dir: Path,
    pairs: list[str],
    timeframe: str,
    timerange: str,
    initial_capital: Decimal | str = Decimal("200"),
    monthly_budget: Decimal | str = Decimal("40"),
    fee: Decimal | str = Decimal("0.004"),
    contribution_day: int = 23,
    weekly_day: str = "monday",
) -> dict[str, Any]:
    """Run identically funded passive deployment methods on local prepared data."""
    start, end = parse_timerange(timerange)
    plan = InvestmentPlan(initial_capital, monthly_budget, fee, contribution_day)
    if weekly_day.lower() not in WEEKDAYS:
        raise ValueError(f"weekly day must be one of: {', '.join(WEEKDAYS)}")
    if not pairs:
        raise ValueError("at least one pair is required")
    events = contribution_schedule(plan, start, end)
    schedule_metadata = [
        {
            "contributed_at": e.contributed_at.isoformat(),
            "amount": _number(e.amount),
            "kind": e.kind,
        }
        for e in events
    ]
    benchmarks, pair_metadata = [], {}
    methods = (
        ("BuyAndHold", "immediate"),
        ("DailyDCA", "daily_dca"),
        ("WeeklyDCA", "weekly_dca"),
    )
    for pair in pairs:
        candles = load_kraken_candles(data_dir, pair, timeframe, timerange)
        pair_metadata[pair] = _candle_metadata(candles, timerange)
        for name, method in methods:
            purchases = _deploy(plan, events, candles, method, WEEKDAYS[weekly_day.lower()])
            result = _build_result(name, pair, candles, events, purchases)
            result["deployment_method"] = method
            result["contribution_schedule"] = schedule_metadata
            benchmarks.append(result)
    total = sum((event.amount for event in events), Decimal("0"))
    return {
        "metadata": {
            "timerange": timerange,
            "timeframe": timeframe,
            "fee": _number(plan.fee_ratio),
            "data_dir": str(data_dir),
            "pairs": pairs,
            "initial_capital": _number(plan.initial_capital),
            "monthly_budget": _number(plan.monthly_budget),
            "contribution_day": plan.contribution_day,
            "contribution_schedule": schedule_metadata,
            "total_contributions": _number(total),
            "pair_candle_coverage": pair_metadata,
        },
        "benchmarks": benchmarks,
    }


def write_details(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for benchmark in result["benchmarks"]:
        stem = (
            benchmark["benchmark"].replace("And", "-and-").replace("DCA", "-dca").lower()
            + "-"
            + benchmark["pair"].replace("/", "-").lower()
        )
        for suffix, rows in (
            ("equity", benchmark["equity_curve"]),
            ("purchase-ledger", benchmark["purchase_ledger"]),
        ):
            # A header-only ledger is still an artifact for a run with no eligible buys.
            fieldnames = list(rows[0]) if rows else PURCHASE_LEDGER_FIELDS
            with (output_dir / f"{stem}-{suffix}.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/kraken"))
    parser.add_argument("--pairs", nargs="+", default=["BTC/EUR", "ETH/EUR"])
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--timerange", required=True)
    parser.add_argument("--initial-capital", default="200")
    parser.add_argument("--fee", default="0.004")
    parser.add_argument("--monthly-budget", default="40")
    parser.add_argument("--contribution-day", type=int, default=23)
    parser.add_argument("--weekly-day", default="monday")
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    legacy_options = ("--daily-contribution", "--weekly-contribution")
    if any(
        argument == option or argument.startswith(f"{option}=")
        for argument in sys.argv[1:]
        for option in legacy_options
    ):
        parser.error(
            "--daily-contribution and --weekly-contribution were removed; use --monthly-budget"
        )
    args = parser.parse_args()
    result = run_passive_benchmarks(
        args.data_dir,
        args.pairs,
        args.timeframe,
        args.timerange,
        args.initial_capital,
        args.monthly_budget,
        args.fee,
        args.contribution_day,
        args.weekly_day,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    if args.output_dir:
        write_details(result, args.output_dir)


if __name__ == "__main__":
    main()
