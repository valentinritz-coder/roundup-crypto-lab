"""Orchestrate strict validation of one active strategy result."""

from __future__ import annotations

from decimal import Decimal

from roundup_crypto_lab.active_common import (
    OPEN_POSITION_STATES,
    SCHEMA_VERSION,
    _mapping,
    _nonnegative,
    _rows,
    dec,
)
from roundup_crypto_lab.active_contributions import _validate_contributions
from roundup_crypto_lab.active_curve import _validate_curve
from roundup_crypto_lab.active_experiment import _validate_experiment
from roundup_crypto_lab.active_trades import _validate_trades


def validate_active_result(payload: dict[str, object], **expected: object) -> None:
    """Validate one active result from experiment metadata through every ledger row."""
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    experiment = _mapping(payload.get("experiment"), "experiment")
    metrics = _mapping(payload.get("adapter_metrics"), "adapter metrics")
    native_metrics = _mapping(payload.get("native_freqtrade_metrics"), "native metrics")
    if native_metrics:
        raise ValueError("active result must not embed native metrics")
    limitations = _rows(payload.get("known_limitations"), "known limitations", nonempty=True)
    if any(not isinstance(item, str) or not item for item in limitations):
        raise ValueError("known limitations must be non-empty strings")
    start, end = _validate_experiment(experiment, expected)
    schedule = _rows(payload.get("contribution_schedule"), "contribution schedule")
    ledger = _rows(payload.get("contribution_ledger"), "contribution ledger")
    trade_values = _rows(payload.get("trade_ledger"), "trade ledger")
    curve = _rows(payload.get("equity_curve"), "equity curve", nonempty=True)
    total_contributed, credited_events = _validate_contributions(schedule, ledger, start, end)
    fees, deployed, exits, trades = _validate_trades(trade_values, start, end)
    last, neutral_return, neutral_drawdown = _validate_curve(
        curve, trades, credited_events, start, end
    )

    nonnegative_metric_names = (
        "total_contributed_capital",
        "free_cash",
        "crypto_value",
        "current_deployed_capital",
        "cumulative_gross_deployed",
        "final_equity",
        "fees_paid",
    )
    for name in nonnegative_metric_names:
        _nonnegative(metrics.get(name), name)
    final_pairs = {
        "free_cash": "free_cash",
        "crypto_value": "crypto_value",
        "current_deployed_capital": "current_deployed_capital",
        "cumulative_gross_deployed": "cumulative_gross_deployed",
        "final_equity": "equity",
        "investment_return": "investment_return",
    }
    if any(
        dec(metrics[key], key) != dec(last[row_key], row_key)
        for key, row_key in final_pairs.items()
    ):
        raise ValueError("final metrics differ from equity curve")
    if dec(metrics.get("total_contributed_capital"), "total contributed") != total_contributed:
        raise ValueError("total contributions differ")
    if dec(metrics.get("investment_return"), "investment return") != (
        dec(metrics.get("final_equity"), "final equity") - total_contributed
    ):
        raise ValueError("final investment return differs")
    if dec(metrics.get("fees_paid"), "fees paid") != fees:
        raise ValueError("fees paid differ from trade ledger")
    if metrics.get("entry_count") != len(trades):
        raise ValueError("entry count differs")
    if metrics.get("exit_count") != sum(exits.values()):
        raise ValueError("exit count differs")
    if metrics.get("exit_reason_counts") != exits:
        raise ValueError("exit reason counts differ")
    if dec(metrics.get("contribution_neutral_return"), "neutral return") != neutral_return:
        raise ValueError("contribution-neutral return differs")
    if (
        dec(metrics.get("contribution_neutral_max_drawdown"), "neutral drawdown")
        != neutral_drawdown
    ):
        raise ValueError("contribution-neutral drawdown differs")
    state = metrics.get("open_position_state")
    open_trades = [trade for trade in trades if trade["_exit_at"] is None]
    if state not in OPEN_POSITION_STATES:
        raise ValueError("unsupported open-position state")
    expected_state = "open_marked_at_final_close" if open_trades else "closed"
    if (
        state != expected_state
        or dec(metrics.get("current_deployed_capital"), "deployed") != deployed
    ):
        raise ValueError("position state differs")
    if not open_trades and dec(metrics.get("crypto_value"), "crypto value") != 0:
        raise ValueError("closed final state retains crypto value")
