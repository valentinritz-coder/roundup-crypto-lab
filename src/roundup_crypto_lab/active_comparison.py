"""Versioned recurring-investor result artifacts and controlled comparison reporting."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER, validate_comparison
from roundup_crypto_lab.all_strategy_comparison import summary as native_summary
from roundup_crypto_lab.passive_benchmarks import parse_timerange

SCHEMA_VERSION = "active-strategy-result/v1"
SUPPORTED_EXIT_REASONS = frozenset({"exit_signal", "stop_loss"})


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal") from error
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def _time(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def build_active_result(
    result: dict[str, object],
    *,
    strategy: str,
    pair: str,
    timeframe: str,
    timerange: str,
    execution_model: str,
    effective_settings: dict[str, object],
) -> dict[str, object]:
    """Wrap adapter output in the stable, deliberately non-native result family."""
    start, end = parse_timerange(timerange)
    trades = result["trades"]
    assert isinstance(trades, list)
    exits: dict[str, int] = {}
    for trade in trades:
        assert isinstance(trade, dict)
        reason = trade.get("exit_reason")
        if reason:
            exits[str(reason)] = exits.get(str(reason), 0) + 1
    curve = result["equity_curve"]
    assert isinstance(curve, list) and curve
    final = curve[-1]
    assert isinstance(final, dict)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": {
            "strategy": strategy,
            "selected_pair": pair,
            "timeframe": timeframe,
            "timerange": timerange,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "capital_mode": result["capital_mode"],
            "investment_plan": result["investment_plan"],
            "effective_settings": effective_settings,
            "execution_model": execution_model,
        },
        "native_freqtrade_metrics": {},
        "adapter_metrics": {
            "total_contributed_capital": result["total_contributed_capital"],
            "free_cash": result["free_cash"],
            "current_deployed_capital": result["current_deployed_capital"],
            "cumulative_gross_deployed": result["cumulative_gross_deployed"],
            "crypto_value": final["crypto_value"],
            "final_equity": result["final_equity"],
            "investment_return": result["investment_return"],
            "fees_paid": result["fees_paid"],
            "entry_count": len(trades),
            "exit_count": sum(exits.values()),
            "exit_reason_counts": exits,
            "contribution_neutral_return": result["contribution_neutral_return"],
            "contribution_neutral_max_drawdown": result["contribution_neutral_max_drawdown"],
            "open_position_state": result["end_of_range_position"],
        },
        "contribution_schedule": result["contribution_schedule"],
        "contribution_ledger": result["contribution_ledger"],
        "trade_ledger": trades,
        "equity_curve": curve,
        "known_limitations": [
            "Dry-run research adapter; not byte-for-byte native Freqtrade output.",
            "Recurring cash flows are excluded from contribution-neutral return.",
        ],
    }


def validate_active_result(
    payload: dict[str, object],
    *,
    strategy: str | None = None,
    pair: str | None = None,
    capital_mode: str | None = None,
    investment_plan: dict[str, str] | None = None,
) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported active result schema version")
    experiment = payload.get("experiment")
    metrics = payload.get("adapter_metrics")
    if not isinstance(experiment, dict) or not isinstance(metrics, dict):
        raise ValueError("active result lacks experiment or adapter metrics")
    if strategy and experiment.get("strategy") != strategy:
        raise ValueError("unexpected strategy")
    if pair and experiment.get("selected_pair") != pair:
        raise ValueError("unexpected selected pair")
    if capital_mode and experiment.get("capital_mode") != capital_mode:
        raise ValueError("unexpected capital mode")
    if experiment.get("timeframe") != "4h":
        raise ValueError("unsupported timeframe")
    start, end = _time(experiment.get("start"), "start"), _time(experiment.get("end"), "end")
    if start >= end or parse_timerange(str(experiment.get("timerange"))) != (start, end):
        raise ValueError("inconsistent timerange")
    if investment_plan and experiment.get("investment_plan") != investment_plan:
        raise ValueError("unexpected investment plan")
    schedule, ledger, trades, curve = (
        payload.get(k)
        for k in ("contribution_schedule", "contribution_ledger", "trade_ledger", "equity_curve")
    )
    if not all(isinstance(x, list) for x in (schedule, ledger, trades, curve)) or not curve:
        raise ValueError("missing ledgers or equity curve")
    schedule_total = sum(
        (_decimal(x["amount"], "schedule amount") for x in schedule if isinstance(x, dict)),
        Decimal(),
    )
    if schedule_total != _decimal(metrics.get("total_contributed_capital"), "total contributions"):
        raise ValueError("contributions do not equal schedule")
    for rows, timestamp_key in (
        (schedule, "contributed_at"),
        (ledger, "investor_contribution_timestamp"),
        (trades, "entry_timestamp"),
        (curve, "timestamp"),
    ):
        previous = start
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("ledger row must be an object")
            value = _time(row[timestamp_key], "row timestamp")
            if not start <= value < end or value < previous:
                raise ValueError("ledgers must be chronological and inside timerange")
            previous = value
    for row in curve:
        assert isinstance(row, dict)
        if _decimal(row["equity"], "equity") != _decimal(row["free_cash"], "free cash") + _decimal(
            row["crypto_value"], "crypto value"
        ):
            raise ValueError("equity does not equal cash plus crypto")
    for name in (
        "free_cash",
        "crypto_value",
        "current_deployed_capital",
        "cumulative_gross_deployed",
        "final_equity",
        "investment_return",
        "fees_paid",
        "contribution_neutral_return",
        "contribution_neutral_max_drawdown",
    ):
        if _decimal(metrics.get(name), name) < 0 and name in {
            "free_cash",
            "crypto_value",
            "current_deployed_capital",
            "cumulative_gross_deployed",
            "fees_paid",
            "contribution_neutral_max_drawdown",
        }:
            raise ValueError(f"{name} must be non-negative")
    if (
        _decimal(metrics["investment_return"], "investment return")
        != _decimal(metrics["final_equity"], "equity") - schedule_total
    ):
        raise ValueError("investment return is inconsistent")
    open_trades = 0
    for trade in trades:
        assert isinstance(trade, dict)
        if _decimal(trade["entry_gross_stake"], "stake") + _decimal(
            trade["entry_fee"], "entry fee"
        ) > _decimal(trade["cash_available"], "available cash"):
            raise ValueError("buy exceeds available cash")
        if trade.get("exit_reason") is None:
            open_trades += 1
        elif trade["exit_reason"] not in SUPPORTED_EXIT_REASONS:
            raise ValueError("unsupported exit reason")
    if open_trades > 1 or (open_trades == 0) != (metrics["open_position_state"] == "closed"):
        raise ValueError("open-position state is inconsistent")
    expected_deployed = (
        _decimal(trades[-1]["entry_gross_stake"], "stake") if open_trades else Decimal()
    )
    if _decimal(metrics["current_deployed_capital"], "deployed capital") != expected_deployed:
        raise ValueError("deployed capital does not match open position")


def _summary(
    active: list[dict[str, object]], native: list[dict[str, object]], metadata: dict[str, Any]
) -> str:
    native_reference = native_summary(native, metadata).replace(
        "# All strategy comparison", "# Native Freqtrade one-shot reference", 1
    )
    lines = [
        native_reference,
        "",
        "# Active investor cash-flow simulation",
        "",
        "| Strategy | Total contributed | Final equity | Investment return | Free cash | "
        "Crypto value | Contribution-neutral return | Contribution-neutral drawdown | "
        "Entries | Exits | Stop exits | Position |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in active:
        e, m = item["experiment"], item["adapter_metrics"]
        assert isinstance(e, dict) and isinstance(m, dict)
        lines.append(
            (
                "| {strategy} | {total_contributed_capital} | {final_equity} | "
                "{investment_return} | {free_cash} | {crypto_value} | "
                "{contribution_neutral_return} | {contribution_neutral_max_drawdown} | "
                "{entry_count} | {exit_count} | {stop} | {open_position_state} |"
            ).format(strategy=e["strategy"], stop=m["exit_reason_counts"].get("stop_loss", 0), **m)
        )
    lines += [
        "",
        "Recurring strategies are ranked only by compatible contribution-neutral return and "
        "drawdown; native one-shot profit_total is not used.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-result", action="append", default=[], type=Path)
    parser.add_argument("--native-comparison", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    active = [json.loads(path.read_text()) for path in args.active_result]
    if len(active) != len(STRATEGY_ORDER):
        raise ValueError("exactly seven active results are required")
    for expected, result in zip(STRATEGY_ORDER, active, strict=True):
        validate_active_result(result, strategy=expected)
    native, metadata = (
        validate_comparison(args.native_comparison),
        json.loads(args.metadata.read_text()),
    )
    args.output.write_text(
        json.dumps(
            {
                "schema_version": "controlled-comparison/v1",
                "native_freqtrade_one_shot_reference": native,
                "active_investor_cash_flow_simulation": active,
            },
            indent=2,
        )
        + "\n"
    )
    args.summary.write_text(_summary(active, native, metadata))


if __name__ == "__main__":
    main()
