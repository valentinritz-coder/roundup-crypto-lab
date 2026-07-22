"""Strict, testable validation for Freqtrade 2026.6 analysis artifacts."""

from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any

STRATEGY = "RoundupBreakoutStrategy"
_LOOKAHEAD_COLUMNS = {
    "strategy",
    "has_bias",
    "biased_entry_signals",
    "biased_exit_signals",
    "biased_indicators",
}


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Report is missing: {path}")
    if not path.stat().st_size:
        raise ValueError(f"Report is empty: {path}")


def validate_lookahead_report(path: Path, strategy: str = STRATEGY) -> None:
    """Reject missing/malformed 2026.6 exports and every reported signal bias."""
    _require_file(path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(reader.fieldnames) >= _LOOKAHEAD_COLUMNS:
            raise ValueError("Lookahead CSV is missing required Freqtrade 2026.6 columns")
        rows = list(reader)
    row = next((item for item in rows if item.get("strategy") == strategy), None)
    if row is None:
        raise ValueError(f"Lookahead CSV has no row for strategy {strategy}")
    try:
        entry = int(row["biased_entry_signals"])
        exit_ = int(row["biased_exit_signals"])
    except ValueError as exc:
        raise ValueError("Lookahead bias signal counts must be integers") from exc
    has_bias = row["has_bias"].strip().lower()
    if has_bias not in {"false", "no", "0"} or entry or exit_ or row["biased_indicators"].strip():
        raise ValueError(f"Lookahead bias detected for {strategy}: {row}")


def validate_recursive_report(path: Path, startups: tuple[int, ...] = (120, 240, 480)) -> None:
    """Validate the text-table contract emitted by Freqtrade 2026.6.

    A report may explicitly say that no variance was found. Otherwise it must contain
    the Rich ``Recursive Analysis`` table with every requested startup column and all
    reported percentage differences must be exactly 0.000%; any variation is rejected.
    """
    _require_file(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if "No variance on indicator(s) found due to recursive formula." in text:
        return
    if "Recursive Analysis" not in text:
        raise ValueError("Recursive report contains neither a stable result nor its result table")
    header = next((line for line in text.splitlines() if "Indicators" in line and "│" in line), "")
    missing = [str(value) for value in startups if not re.search(rf"(?<!\d){value}(?!\d)", header)]
    if missing:
        raise ValueError(
            f"Recursive report is missing startup candle columns: {', '.join(missing)}"
        )
    percentages = re.findall(r"([+-]?\d+(?:\.\d+)?)%", text)
    if not percentages:
        raise ValueError("Recursive report table contains no percentage results")
    if any(float(value) != 0.0 for value in percentages):
        raise ValueError("Recursive analysis reported unstable indicator values")


def _load_backtest(path: Path) -> dict[str, Any]:
    _require_file(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            candidates = []
            for name in archive.namelist():
                if (
                    not name.endswith(".json")
                    or name.endswith(".meta.json")
                    or name.endswith("_config.json")
                ):
                    continue
                document = json.loads(archive.read(name))
                if isinstance(document, dict) and "strategy" in document:
                    candidates.append((name, document))
            if not candidates:
                raise ValueError("Backtest ZIP contains no primary result JSON with a strategy key")
            if len(candidates) != 1:
                names = ", ".join(name for name, _ in candidates)
                raise ValueError(f"Backtest ZIP has ambiguous primary result JSON files: {names}")
            return candidates[0][1]
    return json.loads(path.read_text(encoding="utf-8"))


def create_baseline_summary(
    backtest_path: Path,
    output_path: Path,
    *,
    timerange: str,
    pairs: list[str],
    timeframe: str,
    freqtrade_version: str,
    freqtrade_commit: str,
    repository_commit: str,
    cache_manifest: Path,
    strategy: str = STRATEGY,
) -> dict[str, Any]:
    """Create a stable machine-readable baseline from Freqtrade's exported result."""
    data = _load_backtest(backtest_path)
    results = data.get("strategy", {}).get(strategy)
    if not isinstance(results, dict):
        raise ValueError(f"Backtest result has no strategy {strategy}")
    required = (
        "total_trades",
        "wins",
        "losses",
        "winrate",
        "profit_total",
        "profit_total_abs",
        "profit_factor",
        "max_drawdown_account",
        "starting_balance",
        "final_balance",
    )
    if any(key not in results for key in required):
        raise ValueError("Backtest result is missing required strategy metrics")
    if int(results["total_trades"]) <= 0:
        raise ValueError("Backtest result has zero trades")
    numeric = [results[key] for key in required if key not in {"total_trades", "wins", "losses"}]
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in numeric
    ):
        raise ValueError("Backtest result has non-finite metrics")
    summary = {
        "strategy": strategy,
        "timerange": timerange,
        "pairs": pairs,
        "timeframe": timeframe,
        "trades": results["total_trades"],
        "wins": results["wins"],
        "losses": results["losses"],
        "win_rate_ratio": results["winrate"],
        "win_rate_pct": results["winrate"] * 100,
        "total_profit_ratio": results["profit_total"],
        "total_profit_pct": results["profit_total"] * 100,
        "total_profit_abs": results["profit_total_abs"],
        "profit_factor": results["profit_factor"],
        "max_drawdown_pct": results["max_drawdown_account"] * 100,
        "starting_balance": results["starting_balance"],
        "final_balance": results["final_balance"],
        "freqtrade_version": freqtrade_version,
        "freqtrade_commit": freqtrade_commit,
        "repository_commit": repository_commit,
        "cache_manifest": str(cache_manifest),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def validate_baseline_summary(path: Path, strategy: str = STRATEGY) -> None:
    _require_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "strategy",
        "trades",
        "win_rate_ratio",
        "win_rate_pct",
        "total_profit_ratio",
        "total_profit_pct",
        "timerange",
        "pairs",
        "timeframe",
    }
    if not required <= data.keys() or data["strategy"] != strategy or int(data["trades"]) <= 0:
        raise ValueError(
            "Baseline summary is missing essential fields, has zero trades, or wrong strategy"
        )
    if not isinstance(data["total_profit_pct"], (int, float)) or not math.isfinite(
        data["total_profit_pct"]
    ):
        raise ValueError("Baseline summary total_profit_pct must be finite")
