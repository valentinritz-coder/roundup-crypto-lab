"""Generate and aggregate rolling KerADX versus DCA comparisons."""

from __future__ import annotations

import argparse
import csv
import json
from calendar import monthrange
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

DATE_FORMAT = "%Y-%m-%d"
STRATEGY = "RoundupTrendQualityKerAdxStrategy"
DCA_NAMES = ("DailyDCA", "WeeklyDCA", "MonthlyDCA")


def parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, DATE_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD: {value}") from exc


def add_months(value: datetime, months: int) -> datetime:
    if months <= 0:
        raise ValueError("months must be positive")
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def generate_windows(
    start: datetime,
    end: datetime,
    window_months: int,
    step_months: int,
) -> list[dict[str, str]]:
    if start >= end:
        raise ValueError("start date must precede end date")
    if window_months <= 0 or step_months <= 0:
        raise ValueError("window and step months must be positive")
    windows: list[dict[str, str]] = []
    current = start
    while True:
        window_end = add_months(current, window_months)
        if window_end > end:
            break
        start_text = current.strftime("%Y%m%d")
        end_text = window_end.strftime("%Y%m%d")
        windows.append(
            {
                "window_id": f"{start_text}-{end_text}",
                "start": current.strftime(DATE_FORMAT),
                "end": window_end.strftime(DATE_FORMAT),
                "timerange": f"{start_text}-{end_text}",
            }
        )
        current = add_months(current, step_months)
    if not windows:
        raise ValueError("campaign does not contain any complete rolling window")
    return windows


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _active_row(active: dict[str, Any]) -> dict[str, Any]:
    experiment = active["experiment"]
    metrics = active["cash_flow_metrics"]
    adapter = active["adapter_metrics"]
    return {
        "pair": experiment["selected_pair"],
        "timerange": experiment["timerange"],
        "window_start": experiment["start"],
        "window_end": experiment["end"],
        "total_contributions": metrics["total_contributions"],
        "keradx_final_value": metrics["final_value"],
        "keradx_profit_abs": metrics["profit_abs"],
        "keradx_xirr": metrics["money_weighted_return"],
        "keradx_max_drawdown": metrics["max_drawdown_raw_portfolio"],
        "keradx_fees": metrics["total_fees"],
        "keradx_capital_utilization": metrics["capital_utilization_ratio"],
        "keradx_entry_count": adapter["entry_count"],
        "keradx_exit_count": adapter["exit_count"],
    }


def summarize_window(active_path: Path, passive_path: Path) -> dict[str, Any]:
    active = json.loads(active_path.read_text(encoding="utf-8"))
    passive = json.loads(passive_path.read_text(encoding="utf-8"))
    row = _active_row(active)
    benchmarks = {item["benchmark"]: item for item in passive["benchmarks"]}
    for name in DCA_NAMES:
        if name not in benchmarks:
            raise ValueError(f"missing passive benchmark: {name}")
        metrics = benchmarks[name]["cash_flow_metrics"]
        prefix = name.removesuffix("DCA").lower() + "_dca"
        row[f"{prefix}_final_value"] = metrics["final_value"]
        row[f"{prefix}_xirr"] = metrics["money_weighted_return"]
        row[f"{prefix}_max_drawdown"] = metrics["max_drawdown_raw_portfolio"]
        row[f"{prefix}_fees"] = metrics["total_fees"]
    monthly = _decimal(row["monthly_dca_final_value"])
    keradx = _decimal(row["keradx_final_value"])
    monthly_drawdown = _decimal(row["monthly_dca_max_drawdown"])
    keradx_drawdown = _decimal(row["keradx_max_drawdown"])
    row["keradx_minus_monthly_dca"] = str(keradx - monthly)
    row["keradx_wins_final_value"] = keradx > monthly
    row["keradx_wins_drawdown"] = keradx_drawdown < monthly_drawdown
    return row


def aggregate(
    window_dir: Path,
    output_json: Path,
    output_csv: Path,
    summary_path: Path,
) -> dict[str, Any]:
    rows = []
    for directory in sorted(path for path in window_dir.iterdir() if path.is_dir()):
        active = directory / "active-keradx.json"
        passive = directory / "scenario-passive.json"
        if active.exists() and passive.exists():
            rows.append(summarize_window(active, passive))
    if not rows:
        raise ValueError("no complete rolling-window results found")
    differences = [_decimal(row["keradx_minus_monthly_dca"]) for row in rows]
    drawdown_improvements = [
        _decimal(row["monthly_dca_max_drawdown"])
        - _decimal(row["keradx_max_drawdown"])
        for row in rows
    ]
    payload = {
        "schema_version": "rolling-keradx-dca-comparison/v1",
        "strategy": STRATEGY,
        "window_count": len(rows),
        "keradx_final_value_wins": sum(
            bool(row["keradx_wins_final_value"]) for row in rows
        ),
        "keradx_drawdown_wins": sum(
            bool(row["keradx_wins_drawdown"]) for row in rows
        ),
        "median_final_value_difference": str(median(differences)),
        "median_drawdown_improvement": str(median(drawdown_improvements)),
        "windows": rows,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    count = len(rows)
    summary = (
        "# Rolling KerADX versus DCA\n\n"
        f"- Windows tested: **{count}**\n"
        "- KerADX beats monthly DCA on final value: "
        f"**{payload['keradx_final_value_wins']} / {count}**\n"
        "- KerADX beats monthly DCA on max drawdown: "
        f"**{payload['keradx_drawdown_wins']} / {count}**\n"
        "- Median final-value difference: "
        f"**{payload['median_final_value_difference']} EUR**\n"
        "- Median drawdown improvement: "
        f"**{payload['median_drawdown_improvement']}**\n"
    )
    summary_path.write_text(summary, encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--start-date", required=True)
    generate.add_argument("--end-date", required=True)
    generate.add_argument("--window-months", required=True, type=int)
    generate.add_argument("--step-months", required=True, type=int)
    generate.add_argument("--output", required=True, type=Path)
    collect = subparsers.add_parser("aggregate")
    collect.add_argument("--window-dir", required=True, type=Path)
    collect.add_argument("--output-json", required=True, type=Path)
    collect.add_argument("--output-csv", required=True, type=Path)
    collect.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        windows = generate_windows(
            parse_date(args.start_date),
            parse_date(args.end_date),
            args.window_months,
            args.step_months,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(windows, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        aggregate(args.window_dir, args.output_json, args.output_csv, args.summary)


if __name__ == "__main__":
    main()
