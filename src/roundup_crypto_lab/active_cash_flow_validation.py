"""Rebuild and validate shared cash-flow metrics for active results."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from roundup_crypto_lab.active_common import _mapping, dec
from roundup_crypto_lab.cash_flow_metrics import build_cash_flow_metrics


def validate_cash_flow_metrics(
    value: object,
    *,
    experiment: dict[str, object],
    schedule: list[object],
    curve: list[object],
    fees: Decimal,
    end: datetime,
    neutral_return: Decimal,
    neutral_drawdown: Decimal,
) -> None:
    """Require the published block to equal an independent ledger reconstruction."""
    published = _mapping(value, "cash-flow metrics")
    plan = _mapping(experiment.get("investment_plan"), "investment plan")
    contributions = []
    for value_row in schedule:
        row = _mapping(value_row, "contribution schedule row")
        contributions.append(
            {
                "timestamp": row["contributed_at"],
                "amount": row["amount"],
            }
        )
    snapshots = [
        {
            "timestamp": row["timestamp"],
            "equity": row["equity"],
            "cash": row["free_cash"],
            "asset_value": row["crypto_value"],
            "share_value": row["time_weighted_share_value"],
        }
        for row in (_mapping(item, "equity row") for item in curve)
    ]
    expected = build_cash_flow_metrics(
        initial_capital=plan["initial_capital"],
        monthly_budget=plan["monthly_budget"],
        fee_ratio=plan["fee_ratio"],
        contributions=contributions,
        snapshots=snapshots,
        total_fees=fees,
        period_end=end,
    )
    if published != expected:
        raise ValueError("cash-flow metrics differ from ledgers")
    published_twr = dec(
        published["time_weighted_return"],
        "time-weighted return",
    )
    if published_twr != neutral_return:
        raise ValueError("legacy neutral return differs from TWR")
    published_drawdown = dec(
        published["max_drawdown_time_weighted"],
        "time-weighted drawdown",
    )
    if published_drawdown != neutral_drawdown:
        raise ValueError("legacy neutral drawdown differs from time-weighted drawdown")
