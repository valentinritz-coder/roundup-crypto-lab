"""Create a validated, reproducible comparison of four Freqtrade backtests."""

from __future__ import annotations

import argparse
import json
import math
import zipfile
from pathlib import Path
from typing import Any

REQUIRED_STRATEGIES = {
    "RoundupBreakoutStrategy",
    "RoundupBreakoutTrendStrategy",
    "RoundupBreakoutAtrStrategy",
    "RoundupBreakoutAtrVolumeStrategy",
}
METRICS = (
    "total_trades",
    "profit_total",
    "profit_total_abs",
    "winrate",
    "max_drawdown_account",
    "profit_factor",
    "expectancy",
)


def _read_result(path: Path) -> dict[str, Any]:
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"Backtest result is missing or empty: {path}")
    if path.suffix != ".zip":
        return json.loads(path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(path) as archive:
        candidates = [
            json.loads(archive.read(name))
            for name in archive.namelist()
            if name.endswith(".json")
            and not name.endswith((".meta.json", "_config.json"))
            and "strategy" in json.loads(archive.read(name))
        ]
    if len(candidates) != 1:
        raise ValueError(f"Backtest ZIP must contain exactly one primary result: {path}")
    return candidates[0]


def create_comparison(
    results: dict[str, Path], required_strategies: set[str] | None = None
) -> list[dict[str, int | float | str]]:
    """Return comparable metrics, rejecting incomplete, duplicate, or invalid input."""
    if not results:
        raise ValueError("Comparison report cannot be empty")
    required = REQUIRED_STRATEGIES if required_strategies is None else required_strategies
    if set(results) != required:
        missing = required - set(results)
        extra = set(results) - required
        raise ValueError(
            f"Comparison strategies differ; missing={sorted(missing)}, extra={sorted(extra)}"
        )

    rows: list[dict[str, int | float | str]] = []
    seen: set[str] = set()
    for requested_name, path in results.items():
        strategy_result = _read_result(path).get("strategy", {}).get(requested_name)
        if not isinstance(strategy_result, dict):
            raise ValueError(f"Result is missing strategy {requested_name}: {path}")
        if requested_name in seen:
            raise ValueError(f"Duplicate strategy in comparison: {requested_name}")
        row: dict[str, int | float | str] = {"strategy": requested_name}
        for metric in METRICS:
            value = strategy_result.get(metric)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"Strategy {requested_name} has non-numeric {metric}")
            row["trades" if metric == "total_trades" else metric] = value
        seen.add(requested_name)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="append", required=True, metavar="STRATEGY=PATH")
    parser.add_argument("--expected-strategy", action="append", metavar="STRATEGY")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs: dict[str, Path] = {}
    for item in args.result:
        strategy, separator, raw_path = item.partition("=")
        if not separator or not strategy or not raw_path or strategy in inputs:
            raise SystemExit("Each --result must be a unique STRATEGY=PATH pair")
        inputs[strategy] = Path(raw_path)
    expected = set(args.expected_strategy) if args.expected_strategy else None
    rows = create_comparison(inputs, expected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
