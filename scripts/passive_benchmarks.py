"""Deterministic, long-only passive benchmarks on prepared Kraken OHLCV data.

Purchases use the open of the first candle at or after their scheduled UTC instant.
There is no sale: final values use the last eligible candle close and include no sale fee.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

TIMEFRAME = "4h"
INTERVAL = timedelta(hours=4)
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
    if (frame["date"].diff().dropna() > pd.Timedelta(INTERVAL)).any():
        raise ValueError(f"critical 4h candle gap in {pair}")
    start, end = parse_timerange(timerange)
    selected = frame[(frame["date"] >= start) & (frame["date"] < end)].reset_index(drop=True)
    if selected.empty or selected.iloc[0]["date"].to_pydatetime() != start:
        raise ValueError(f"insufficient Kraken coverage at timerange start for {pair}")
    # A 4h candle beginning on the final date is outside this end-exclusive date timerange.
    if selected.iloc[-1]["date"].to_pydatetime() < end - INTERVAL:
        raise ValueError(f"insufficient Kraken coverage at timerange end for {pair}")
    return selected


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
    by_index = {item["candle_index"]: item for item in purchases}
    running_quantity = Decimal("0")
    for index, candle in candles.iterrows():
        if index in by_index:
            purchase = by_index[index]
            open_value = running_quantity * Decimal(str(candle["open"]))
            share_value = Decimal("1") if shares == 0 else open_value / shares
            shares += purchase["gross_contribution"] / share_value
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
        "max_drawdown": _number(_drawdown([row["portfolio_value"] for row in equity])),
        "max_drawdown_raw_portfolio": _number(
            _drawdown([row["portfolio_value"] for row in equity])
        ),
        "max_drawdown_time_weighted": _number(
            _drawdown([row["time_weighted_share_value"] for row in equity])
        ),
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
                        "quantity": contribution * (Decimal("1") - fee) / price,
                        "candle_index": index,
                    }
                )
                used_indexes.add(index)
        current += timedelta(days=1)
    return _build_result(
        "DailyDCA" if weekly_day is None else "WeeklyDCA", pair, candles, purchases, fee
    )


def run_passive_benchmarks(
    data_dir: Path,
    pairs: list[str],
    timeframe: str,
    timerange: str,
    initial_capital: Decimal | str = Decimal("200"),
    daily_contribution: Decimal | str = Decimal("10"),
    weekly_contribution: Decimal | str = Decimal("10"),
    fee: Decimal | str = Decimal("0.004"),
    weekly_day: str = "monday",
) -> dict[str, Any]:
    """Run six independent benchmark/pair calculations using only prepared local data."""
    parse_timerange(timerange)
    initial, daily, weekly = (
        _decimal(value, name)
        for value, name in (
            (initial_capital, "initial capital"),
            (daily_contribution, "daily contribution"),
            (weekly_contribution, "weekly contribution"),
        )
    )
    fee_value = _decimal(fee, "fee", allow_zero=True)
    if fee_value >= 1:
        raise ValueError("fee must be lower than 1")
    if weekly_day.lower() not in WEEKDAYS:
        raise ValueError(f"weekly day must be one of: {', '.join(WEEKDAYS)}")
    if not pairs:
        raise ValueError("at least one pair is required")
    benchmarks = []
    for pair in pairs:
        candles = load_kraken_candles(data_dir, pair, timeframe, timerange)
        benchmarks.extend(
            (
                buy_and_hold(candles, pair, initial, fee_value),
                dca(candles, pair, daily, fee_value),
                dca(candles, pair, weekly, fee_value, weekly_day=WEEKDAYS[weekly_day.lower()]),
            )
        )
    return {
        "metadata": {
            "timerange": timerange,
            "timeframe": timeframe,
            "fee": _number(fee_value),
            "data_dir": str(data_dir),
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "pairs": pairs,
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
    parser.add_argument("--daily-contribution", default="10")
    parser.add_argument("--weekly-contribution", default="10")
    parser.add_argument("--weekly-day", default="monday")
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = run_passive_benchmarks(
        args.data_dir,
        args.pairs,
        args.timeframe,
        args.timerange,
        args.initial_capital,
        args.daily_contribution,
        args.weekly_contribution,
        args.fee,
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
