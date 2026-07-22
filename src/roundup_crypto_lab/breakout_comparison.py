"""Validation and reporting helpers for the breakout-comparison workflow."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from roundup_crypto_lab.compare_strategies import METRICS, REQUIRED_STRATEGIES

STRATEGY_ORDER = (
    "RoundupBreakoutStrategy",
    "RoundupBreakoutTrendStrategy",
    "RoundupBreakoutAtrStrategy",
    "RoundupBreakoutAtrVolumeStrategy",
)


def parse_timerange(value: str) -> tuple[date, date]:
    """Strictly parse a closed-date Freqtrade timerange with a non-empty interval."""
    if len(value) != 17 or value[8] != "-" or not (value[:8] + value[9:]).isdigit():
        raise ValueError("timerange must use exactly YYYYMMDD-YYYYMMDD")
    try:
        start = datetime.strptime(value[:8], "%Y%m%d").date()
        end = datetime.strptime(value[9:], "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("timerange contains an invalid calendar date") from exc
    if start >= end:
        raise ValueError("timerange start date must be strictly before end date")
    return start, end


def validate_prepared_data(timerange: str, data_directory: Path) -> None:
    """Require the immutable cache to cover the requested 4h comparison interval."""
    requested_start, requested_end = parse_timerange(timerange)
    from roundup_crypto_lab.kraken_ohlcv import common_timerange, load_and_verify_manifest

    load_and_verify_manifest(data_directory)
    available = common_timerange(data_directory)
    available_start, available_end = parse_timerange(available)
    if requested_start < available_start or requested_end > available_end:
        raise ValueError(
            f"Requested {timerange} is outside prepared Kraken coverage {available}. "
            "Run Update Kraken data first."
        )


def validate_comparison(path: Path) -> list[dict[str, int | float | str]]:
    """Read a complete four-strategy comparison and reject non-finite metrics."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != len(STRATEGY_ORDER):
        raise ValueError("comparison must contain exactly four rows")
    names = [row.get("strategy") for row in rows if isinstance(row, dict)]
    if set(names) != REQUIRED_STRATEGIES or len(set(names)) != len(STRATEGY_ORDER):
        raise ValueError("comparison must contain each required strategy exactly once")
    for row in rows:
        for metric in ("trades", *METRICS[1:]):
            value = row.get(metric)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"comparison has non-numeric {metric}")
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"comparison has non-finite {metric}")
    return rows


def validate_metadata(metadata: dict[str, Any]) -> None:
    """Reject incomplete metadata before it is retained with the comparison artifact."""
    required = {
        "timerange",
        "timeframe",
        "commit_sha",
        "python_version",
        "freqtrade_version",
        "run_date_utc",
        "strategies",
    }
    if set(metadata) != required or metadata["strategies"] != list(STRATEGY_ORDER):
        raise ValueError("metadata is incomplete or names unexpected strategies")
    parse_timerange(str(metadata["timerange"]))
    if metadata["timeframe"] != "4h":
        raise ValueError("metadata timeframe must be 4h")
    for key in required - {"timerange", "timeframe", "strategies"}:
        if not isinstance(metadata[key], str) or not metadata[key]:
            raise ValueError(f"metadata {key} must be a non-empty string")


def summary_markdown(rows: list[dict[str, int | float | str]], metadata: dict[str, Any]) -> str:
    """Format raw Freqtrade ratios as percentages only in the job summary."""
    by_name = {str(row["strategy"]): row for row in rows}
    lines = [
        "# Breakout strategy comparison",
        "",
        f"- **Timerange:** `{metadata['timerange']}`",
        f"- **Timeframe:** `{metadata['timeframe']}`",
        f"- **Freqtrade:** `{metadata['freqtrade_version']}`",
        f"- **Commit:** `{metadata['commit_sha']}` ({metadata['run_date_utc']})",
        "",
        "Raw `profit_total`, `winrate`, and `max_drawdown_account` are Freqtrade ratios; "
        "this summary multiplies them by 100 for display only.",
        "",
        "| Strategy | Trades | Profit total % | Profit total abs | Winrate % | "
        "Max drawdown % | Profit factor | Expectancy |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in STRATEGY_ORDER:
        row = by_name[name]
        lines.append(
            "| {name} | {trades} | {profit:.2f}% | {absolute:.8f} | {winrate:.2f}% | "
            "{drawdown:.2f}% | {factor:.4f} | {expectancy:.8f} |".format(
                name=name,
                trades=row["trades"],
                profit=float(row["profit_total"]) * 100,
                absolute=float(row["profit_total_abs"]),
                winrate=float(row["winrate"]) * 100,
                drawdown=float(row["max_drawdown_account"]) * 100,
                factor=float(row["profit_factor"]),
                expectancy=float(row["expectancy"]),
            )
        )
    lines.extend(
        [
            "",
            "These results describe one timerange only and do not establish "
            "out-of-sample profitability.",
            "",
        ]
    )
    return "\n".join(lines)


def _metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "timerange": args.timerange,
        "timeframe": args.timeframe,
        "commit_sha": args.commit_sha,
        "python_version": args.python_version,
        "freqtrade_version": args.freqtrade_version,
        "run_date_utc": args.run_date_utc,
        "strategies": list(STRATEGY_ORDER),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    check_input = subcommands.add_parser("validate-timerange")
    check_input.add_argument("timerange")
    check_data = subcommands.add_parser("validate-data")
    check_data.add_argument("timerange")
    check_data.add_argument("data_directory", type=Path)
    validate = subcommands.add_parser("validate-comparison")
    validate.add_argument("path", type=Path)
    report = subcommands.add_parser("write-report")
    report.add_argument("comparison", type=Path)
    report.add_argument("metadata", type=Path)
    report.add_argument("summary", type=Path)
    for option in (
        "timerange",
        "timeframe",
        "commit-sha",
        "python-version",
        "freqtrade-version",
        "run-date-utc",
    ):
        report.add_argument(f"--{option}", required=True)
    args = parser.parse_args()
    if args.command == "validate-timerange":
        parse_timerange(args.timerange)
    elif args.command == "validate-data":
        validate_prepared_data(args.timerange, args.data_directory)
    elif args.command == "validate-comparison":
        validate_comparison(args.path)
    else:
        parse_timerange(args.timerange)
        metadata = _metadata(args)
        validate_metadata(metadata)
        args.metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        args.summary.write_text(
            summary_markdown(validate_comparison(args.comparison), metadata), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
