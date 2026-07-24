"""Build versioned active strategy result artifacts."""

from __future__ import annotations

from roundup_crypto_lab.active_common import SCHEMA_VERSION, _mapping, _rows, identity
from roundup_crypto_lab.cash_flow_metrics import build_cash_flow_metrics
from roundup_crypto_lab.passive_benchmarks import parse_timerange


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
    """Wrap adapter output in the stable active-result schema."""
    start, end = parse_timerange(timerange)
    trades = _rows(result.get("trades"), "trade ledger")
    curve = _rows(result.get("equity_curve"), "equity curve", nonempty=True)
    final = _mapping(curve[-1], "final equity row")
    exits: dict[str, int] = {}
    for trade_value in trades:
        trade = _mapping(trade_value, "trade")
        reason = trade.get("exit_reason")
        if reason is not None:
            exits[str(reason)] = exits.get(str(reason), 0) + 1
    experiment: dict[str, object] = {
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
        "execution_scope": result.get("execution_scope"),
    }
    experiment["experiment_id"] = identity(experiment)
    recurring = experiment["capital_mode"] == "recurring_monthly_contributions"
    limitations = (
        ["Recurring investor cash flows have no native Freqtrade-equivalent."]
        if recurring
        else ["One-shot equivalence is limited to the validated differential lifecycle scope."]
    )

    plan = _mapping(result.get("investment_plan"), "investment plan")
    schedule = _rows(
        result.get("contribution_schedule"),
        "contribution schedule",
        nonempty=True,
    )
    metric_contributions = []
    for value in schedule:
        row = _mapping(value, "contribution schedule row")
        metric_contributions.append(
            {
                "timestamp": row["contributed_at"],
                "amount": row["amount"],
            }
        )
    metric_snapshots = [
        {
            "timestamp": row["timestamp"],
            "equity": row["equity"],
            "cash": row["free_cash"],
            "asset_value": row["crypto_value"],
            "share_value": row["time_weighted_share_value"],
        }
        for row in (_mapping(value, "equity row") for value in curve)
    ]
    cash_flow_metrics = build_cash_flow_metrics(
        initial_capital=plan["initial_capital"],
        monthly_budget=plan["monthly_budget"],
        fee_ratio=plan["fee_ratio"],
        contributions=metric_contributions,
        snapshots=metric_snapshots,
        total_fees=result["fees_paid"],
        period_end=end,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": experiment,
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
            "contribution_neutral_max_drawdown": result[
                "contribution_neutral_max_drawdown"
            ],
            "open_position_state": result["end_of_range_position"],
        },
        "cash_flow_metrics": cash_flow_metrics,
        "contribution_schedule": result["contribution_schedule"],
        "contribution_ledger": result["contribution_ledger"],
        "trade_ledger": trades,
        "equity_curve": curve,
        "known_limitations": limitations,
    }
