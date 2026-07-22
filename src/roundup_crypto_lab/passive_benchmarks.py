"""Deterministic, long-only passive benchmarks on prepared Kraken OHLCV data.

Purchases use the open of the first candle at or after their scheduled UTC instant.
There is no sale: final values use the last eligible candle close and include no sale fee.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
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
    return maximum


def _purchase_at_or_after(candles: pd.DataFrame, scheduled: datetime) -> tuple[int, Any] | None:
    matching = candles.index[candles["date"] >= scheduled]
    if len(matching) == 0:
        return None
    index = int(matching[0])
    return index, candles.iloc[index]


def _build_result(
    benchmark: str, pair: str, candles: pd.DataFrame, purchases: list[dict[str, Any]], fee: Decimal
) -> dict[str, Any]:
    quantity = sum((item["quantity"] for item in purchases), Decimal("0"))
    invested = sum((item["gross_contribution"] for item in purchases), Decimal("0"))
    fees = sum((item["fee_paid"] for item in purchases), Decimal("0"))
    final_price = Decimal(str(candles.iloc[-1]["close"]))
    final_value = quantity * final_price
    equity, share_value, contribution_total, shares = [], Decimal("1"), Decimal("0"), Decimal("0")
    by_index: dict[int, list[dict[str, Any]]] = {}
    for purchase in purchases:
        by_index.setdefault(purchase["candle_index"], []).append(purchase)
    running_quantity = Decimal("0")
    for index, candle in candles.iterrows():
        for purchase in by_index.get(index, []):
            open_value = running_quantity * Decimal(str(candle["open"]))
            share_value = Decimal("1") if shares == 0 else open_value / shares
            shares += purchase["net_contribution"] / share_value
            contribution_total += purchase["gross_contribution"]
            running_quantity += purchase["quantity"]
        portfolio = running_quantity * Decimal(str(candle["close"]))
        if shares:
            share_value = portfolio / shares
        equity.append(
            {
                "timestamp": candle["date"].to_pydatetime().isoformat(),
                "portfolio_value": portfolio,
                "net_value": portfolio - contribution_total,
                "cumulative_contributions": contribution_total,
                "time_weighted_share_value": share_value,
            }
        )
    average = invested / quantity if quantity else None
    raw_drawdown = _drawdown([row["portfolio_value"] for row in equity])
    time_weighted_drawdown = _drawdown([row["time_weighted_share_value"] for row in equity])
    result = {
        "benchmark": benchmark,
        "category": "benchmark",
        "pair": pair,
        "number_of_buys": len(purchases),
        "capital_invested": _number(invested),
        "total_contributions": _number(invested),
        "fees_paid": _number(fees),
        "quantity": _number(quantity),
        "average_entry_price": _number(average),
        "final_price": _number(final_price),
        "final_value": _number(final_value),
        "profit_total_abs": _number(final_value - invested),
        "profit_total": _number((final_value - invested) / invested),
        "max_drawdown": _number(
            raw_drawdown if benchmark == "BuyAndHold" else time_weighted_drawdown
        ),
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
        "purchases": [
            {
                key: (_number(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
                if key != "candle_index"
            }
            for row in purchases
        ],
    }
    return result


def buy_and_hold(
    candles: pd.DataFrame, pair: str, initial_capital: Decimal, fee: Decimal
) -> dict[str, Any]:
    first = candles.iloc[0]
    price = Decimal(str(first["open"]))
    purchase = {
        "scheduled_at": first["date"].to_pydatetime().isoformat(),
        "executed_at": first["date"].to_pydatetime().isoformat(),
        "execution_price": price,
        "gross_contribution": initial_capital,
        "fee_paid": initial_capital * fee,
        "net_contribution": initial_capital * (Decimal("1") - fee),
        "quantity": initial_capital * (Decimal("1") - fee) / price,
        "candle_index": 0,
    }
    return _build_result("BuyAndHold", pair, candles, [purchase], fee)


def dca(
    candles: pd.DataFrame,
    pair: str,
    contribution: Decimal,
    fee: Decimal,
    *,
    weekly_day: int | None = None,
) -> dict[str, Any]:
    start = candles.iloc[0]["date"].to_pydatetime().astimezone(UTC).date()
    end = candles.iloc[-1]["date"].to_pydatetime().astimezone(UTC).date()
    purchases: list[dict[str, Any]] = []
    current = start
    used_indexes: set[int] = set()
    while current <= end:
        if weekly_day is None or current.weekday() == weekly_day:
            scheduled = datetime.combine(current, datetime.min.time(), tzinfo=UTC)
            matched = _purchase_at_or_after(candles, scheduled)
            if matched and matched[0] not in used_indexes:
                index, candle = matched
                price = Decimal(str(candle["open"]))
                purchases.append(
                    {
                        "scheduled_at": scheduled.isoformat(),
                        "executed_at": candle["date"].to_pydatetime().isoformat(),
                        "execution_price": price,
                        "gross_contribution": contribution,
                        "fee_paid": contribution * fee,
                        "net_contribution": contribution * (Decimal("1") - fee),
                        "quantity": contribution * (Decimal("1") - fee) / price,
                        "candle_index": index,
                    }
                )
                used_indexes.add(index)
        current += timedelta(days=1)
    return _build_result(
        "DailyDCA" if weekly_day is None else "WeeklyDCA", pair, candles, purchases, fee
    )


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
    for position, event in enumerate(events):
        next_at = events[position + 1].contributed_at if position + 1 < len(events) else end
        if method in {"immediate", "monthly_dca"}:
            scheduled = [event.contributed_at]
        else:
            scheduled = [
                date
                for date in _deployment_dates(event.contributed_at, next_at, method, weekly_day)
                if date >= event.contributed_at
            ]
            if not scheduled:
                scheduled = [event.contributed_at]
        amount = event.amount / len(scheduled)
        for scheduled_at in scheduled:
            purchase = _purchase(candles, event, scheduled_at, amount, plan.fee_ratio)
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
        ("MonthlyDCA", "monthly_dca"),
    )
    for pair in pairs:
        candles = load_kraken_candles(data_dir, pair, timeframe, timerange)
        pair_metadata[pair] = _candle_metadata(candles, timerange)
        for name, method in methods:
            purchases = _deploy(plan, events, candles, method, WEEKDAYS[weekly_day.lower()])
            result = _build_result(name, pair, candles, purchases, plan.fee_ratio)
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
            "generated_at_utc": datetime.now(UTC).isoformat(),
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
            ("purchases", benchmark["purchases"]),
        ):
            if rows:
                with (output_dir / f"{stem}-{suffix}.csv").open(
                    "w", newline="", encoding="utf-8"
                ) as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
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
    legacy_options = {"--daily-contribution", "--weekly-contribution"}
    if legacy_options.intersection(sys.argv[1:]):
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
