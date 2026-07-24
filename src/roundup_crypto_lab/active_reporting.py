"""Controlled active-comparison reporting and CLI."""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.active_common import _mapping
from roundup_crypto_lab.active_cross_validation import (
    validate_differential,
    validate_native_metadata,
    validate_result_set,
)
from roundup_crypto_lab.all_strategy_comparison import validate_comparison

CONTROLLED_SCHEMA_VERSION = "controlled-comparison/v1"


def _money(value: object) -> str:
    return f"{Decimal(str(value)):.2f}"


def _percent(value: object) -> str:
    return f"{Decimal(str(value)):.2%}"


def _xirr(metrics: dict[str, Any]) -> str:
    value = metrics.get("money_weighted_return")
    status = metrics.get("money_weighted_return_status")
    return f"N/A ({status})" if value is None else _percent(value)


def render_summary(
    native: list[dict[str, int | float | str]],
    results: list[dict[str, object]],
    experiment: dict[str, Any],
    differential: dict[str, Any] | None,
) -> str:
    native_header = (
        "| Strategy | Trades | Profit total % | Profit abs | Win rate % | "
        "Max drawdown % | Profit factor | Expectancy |"
    )
    native_separator = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    lines = [
        "# Native Freqtrade one-shot reference",
        "",
        native_header,
        native_separator,
    ]
    lines.extend(
        "| {strategy} | {trades} | {profit_total:.2%} | {profit_total_abs:.8f} | "
        "{winrate:.2%} | {max_drawdown_account:.2%} | {profit_factor:.4f} | "
        "{expectancy:.8f} |".format(**row)
        for row in native
    )
    active_header = (
        "| Strategy | Contributed | Final value | Profit | TWR | XIRR | "
        "Utilization | TWR drawdown | Raw drawdown | Fees | Entries | Position |"
    )
    active_separator = (
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | --- |"
    )
    lines += [
        "",
        "# Active investor cash-flow simulation",
        "",
        active_header,
        active_separator,
    ]
    for result in results:
        legacy = _mapping(result.get("adapter_metrics"), "adapter metrics")
        metrics = _mapping(result.get("cash_flow_metrics"), "cash-flow metrics")
        current_experiment = _mapping(result.get("experiment"), "experiment")
        lines.append(
            "| {strategy} | {contributed} | {final_value} | {profit} | {twr} | {xirr} | "
            "{utilization} | {twr_drawdown} | {raw_drawdown} | {fees} | {entries} | "
            "{position} |".format(
                strategy=current_experiment["strategy"],
                contributed=_money(metrics["total_contributions"]),
                final_value=_money(metrics["final_value"]),
                profit=_money(metrics["profit_abs"]),
                twr=_percent(metrics["time_weighted_return"]),
                xirr=_xirr(metrics),
                utilization=_percent(metrics["capital_utilization_ratio"]),
                twr_drawdown=_percent(metrics["max_drawdown_time_weighted"]),
                raw_drawdown=_percent(metrics["max_drawdown_raw_portfolio"]),
                fees=_money(metrics["total_fees"]),
                entries=legacy["entry_count"],
                position=legacy["open_position_state"],
            )
        )
    if experiment["capital_mode"] == "recurring_monthly_contributions":
        lines += [
            "",
            "TWR and time-weighted drawdown describe method performance without "
            "contribution timing. Final value, profit, and XIRR describe the investor "
            "outcome. Native one-shot profit is not used to rank recurring simulations.",
        ]
    elif differential is not None:
        lines += [
            "",
            "# One-shot differential validation",
            "",
            f"Experiment ID: `{differential['experiment_id']}`",
            "",
            "| Strategy | Status | Trades checked | Warnings |",
            "| --- | --- | ---: | ---: |",
        ]
        for row in differential["strategies"]:
            warning_count = len(row.get("warnings", []))
            lines.append(
                f"| {row['strategy']} | {row['status']} | "
                f"{row['trade_count']} | {warning_count} |"
            )
        warning_rows = [
            row
            for row in differential["strategies"]
            if row.get("status") == "passed_with_warnings"
        ]
        if warning_rows:
            lines += [
                "",
                "Warning-only results preserve identical trade counts, timestamps, and "
                "exit reasons. They record bounded rounding or supported intrabar "
                "stop-model differences in the artifact.",
            ]
        lines += [
            "",
            "This proves only the documented one-shot lifecycle and economically bounded "
            "final-balance scope; it is not a general Freqtrade-equivalence claim.",
        ]
    return "\n".join(lines) + "\n"


def write_cash_flow_csv(results: list[dict[str, object]], path: Path) -> None:
    """Write one flat metric row per active strategy."""
    rows = []
    for result in results:
        experiment = _mapping(result.get("experiment"), "experiment")
        legacy = _mapping(result.get("adapter_metrics"), "adapter metrics")
        metrics = _mapping(result.get("cash_flow_metrics"), "cash-flow metrics")
        rows.append(
            {
                "category": "active",
                "method": experiment["strategy"],
                "pair": experiment["selected_pair"],
                "capital_mode": experiment["capital_mode"],
                "number_of_actions": legacy["entry_count"],
                **metrics,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active-result", action="append", type=Path, required=True)
    parser.add_argument("--native-comparison", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--one-shot-differential", type=Path)
    args = parser.parse_args()

    results = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.active_result
    ]
    experiment = validate_result_set(results)
    native = validate_comparison(args.native_comparison)
    metadata = _mapping(
        json.loads(args.metadata.read_text(encoding="utf-8")),
        "native metadata",
    )
    validate_native_metadata(metadata, experiment)

    differential: dict[str, Any] | None = None
    one_shot = experiment["capital_mode"] == "one_shot_capital"
    if one_shot and not args.one_shot_differential:
        raise ValueError("one-shot comparison requires differential artifact")
    if not one_shot and args.one_shot_differential:
        raise ValueError("recurring comparison must not include differential artifact")
    if args.one_shot_differential:
        differential = _mapping(
            json.loads(args.one_shot_differential.read_text(encoding="utf-8")),
            "one-shot differential",
        )
        validate_differential(differential, experiment)

    output = {
        "schema_version": CONTROLLED_SCHEMA_VERSION,
        "cash_flow_metrics_schema_version": "cash-flow-metrics/v1",
        "experiment": experiment,
        "native_metadata": metadata,
        "native_freqtrade_one_shot_reference": native,
        "active_investor_cash_flow_simulation": results,
        **({"one_shot_differential": differential} if differential else {}),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, default=str, indent=2) + "\n",
        encoding="utf-8",
    )
    args.summary.write_text(
        render_summary(native, results, experiment, differential),
        encoding="utf-8",
    )
    write_cash_flow_csv(
        results,
        args.csv or args.output.with_name("cash-flow-metrics.csv"),
    )
