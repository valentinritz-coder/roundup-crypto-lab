"""Reporting helpers for the seven-strategy, controlled comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from roundup_crypto_lab.breakout_comparison import parse_timerange
from roundup_crypto_lab.compare_strategies import METRICS

STRATEGY_ORDER = (
    "RoundupBreakoutStrategy",
    "RoundupBreakoutTrendStrategy",
    "RoundupBreakoutAtrStrategy",
    "RoundupBreakoutAtrVolumeStrategy",
    "RoundupTrendPullbackStrategy",
    "RoundupConfirmedBreakoutStrategy",
    "RoundupVolatilitySqueezeStrategy",
)


def validate_comparison(path: Path) -> list[dict[str, int | float | str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != len(STRATEGY_ORDER):
        raise ValueError("comparison must contain exactly seven rows")
    if [row.get("strategy") for row in rows] != list(STRATEGY_ORDER):
        raise ValueError("comparison strategies must be complete and ordered")
    for row in rows:
        for metric in ("trades", *METRICS[1:]):
            value = row.get(metric)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value != value
                or value in (float("inf"), float("-inf"))
            ):
                raise ValueError(f"comparison has non-finite {metric}")
    return rows


def summary(rows: list[dict[str, int | float | str]], metadata: dict[str, Any]) -> str:
    lines = [
        "# All strategy comparison",
        "",
        *(
            f"- **{key}:** `{metadata[key.lower().replace(' ', '_')]}`"
            for key in (
                "Timerange",
                "Timeframe",
                "Commit SHA",
                "Freqtrade version",
                "Python version",
                "Run date UTC",
            )
        ),
        "",
        "| Strategy | Trades | Profit total % | Profit abs | Winrate % | Max drawdown % | Profit factor | Expectancy |",  # noqa: E501
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            (
                "| {strategy} | {trades} | {profit_total:.2%} | {profit_total_abs:.8f} | {winrate:.2%} | {max_drawdown_account:.2%} | {profit_factor:.4f} | {expectancy:.8f} |"  # noqa: E501
            ).format(**row)
        )
    for title, key, reverse in (
        ("Best profit total", "profit_total", True),
        ("Best profit factor", "profit_factor", True),
        ("Lowest drawdown", "max_drawdown_account", False),
    ):
        lines.extend(["", f"## Ranking: {title}"])
        lines.extend(
            f"{index}. {row['strategy']} ({float(row[key]):.6f})"
            for index, row in enumerate(
                sorted(rows, key=lambda row: float(row[key]), reverse=reverse), 1
            )
        )
    lines.extend(
        [
            "",
            "These results describe one timerange only and do not establish out-of-sample profitability.",  # noqa: E501
            "",
            "No strategy should be selected for live trading from this comparison alone.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison", type=Path)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("summary", type=Path)
    for option in (
        "timerange",
        "timeframe",
        "commit-sha",
        "python-version",
        "freqtrade-version",
        "ccxt-version",
        "run-date-utc",
        "config",
        "pairs",
        "fee",
        "starting-balance",
    ):
        parser.add_argument(f"--{option}", required=True)
    args = parser.parse_args()
    parse_timerange(args.timerange)
    if args.timeframe != "4h":
        raise ValueError("timeframe must be 4h")
    metadata = {
        key.replace("-", "_"): getattr(args, key.replace("-", "_"))
        for key in (
            "timerange",
            "timeframe",
            "commit-sha",
            "python-version",
            "freqtrade-version",
            "ccxt-version",
            "run-date-utc",
            "config",
            "pairs",
            "fee",
            "starting-balance",
        )
    }
    metadata["strategies"] = list(STRATEGY_ORDER)
    rows = validate_comparison(args.comparison)
    args.metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    args.summary.write_text(summary(rows, metadata), encoding="utf-8")


if __name__ == "__main__":
    main()
