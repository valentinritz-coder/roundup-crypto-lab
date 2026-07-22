"""Controlled active-comparison reporting and CLI."""

from __future__ import annotations

import argparse
import json
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


def render_summary(
    native: list[dict[str, int | float | str]],
    results: list[dict[str, object]],
    experiment: dict[str, Any],
    differential: dict[str, Any] | None,
) -> str:
    lines = [
        "# Native Freqtrade one-shot reference",
        "",
        "| Strategy | Trades | Profit total % | Profit abs | Win rate % | Max drawdown % | Profit factor | Expectancy |",  # noqa: E501
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        "| {strategy} | {trades} | {profit_total:.2%} | {profit_total_abs:.8f} | {winrate:.2%} | {max_drawdown_account:.2%} | {profit_factor:.4f} | {expectancy:.8f} |".format(  # noqa: E501
            **row
        )
        for row in native
    )
    lines += [
        "",
        "# Active investor cash-flow simulation",
        "",
        "| Strategy | Contributed | Final equity | Investment return | Free cash | Crypto value | Neutral return | Neutral drawdown | Entries | Exits | Stop exits | Position |",  # noqa: E501
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        metrics = _mapping(result.get("adapter_metrics"), "adapter metrics")
        current_experiment = _mapping(result.get("experiment"), "experiment")
        lines.append(
            "| {strategy} | {total_contributed_capital} | {final_equity} | {investment_return} | {free_cash} | {crypto_value} | {contribution_neutral_return} | {contribution_neutral_max_drawdown} | {entry_count} | {exit_count} | {stop_exits} | {open_position_state} |".format(  # noqa: E501
                strategy=current_experiment["strategy"],
                stop_exits=_mapping(metrics["exit_reason_counts"], "exit reasons").get(
                    "stop_loss", 0
                ),
                **metrics,
            )
        )
    if experiment["capital_mode"] == "recurring_monthly_contributions":
        lines += [
            "",
            "Recurring simulations are comparable only under this identical experiment and are interpreted using contribution-neutral metrics. Native one-shot profit is not used to rank them.",  # noqa: E501
        ]
    elif differential is not None:
        lines += [
            "",
            "# One-shot differential validation",
            "",
            f"Experiment ID: `{differential['experiment_id']}`",
            "",
            "| Strategy | Status | Trades checked | Lifecycle | Final balances |",
            "| --- | --- | ---: | --- | --- |",
        ]
        lines.extend(
            f"| {row['strategy']} | {row['status']} | {row['trade_count']} | passed | passed |"
            for row in differential["strategies"]
        )
        lines += [
            "",
            "This proves only the documented one-shot lifecycle and final-balance scope; it is not a general Freqtrade-equivalence claim.",  # noqa: E501
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active-result", action="append", type=Path, required=True)
    parser.add_argument("--native-comparison", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--one-shot-differential", type=Path)
    args = parser.parse_args()

    results = [json.loads(path.read_text(encoding="utf-8")) for path in args.active_result]
    experiment = validate_result_set(results)
    native = validate_comparison(args.native_comparison)
    metadata = _mapping(json.loads(args.metadata.read_text(encoding="utf-8")), "native metadata")
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
        "experiment": experiment,
        "native_metadata": metadata,
        "native_freqtrade_one_shot_reference": native,
        "active_investor_cash_flow_simulation": results,
        **({"one_shot_differential": differential} if differential else {}),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, default=str, indent=2) + "\n", encoding="utf-8")
    args.summary.write_text(
        render_summary(native, results, experiment, differential), encoding="utf-8"
    )
