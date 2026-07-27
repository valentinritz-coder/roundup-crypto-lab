"""Run one reproducible controlled comparison of registered DCA strategies."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.cash_flow_metrics import build_cash_flow_metrics
from roundup_crypto_lab.dca_baselines import DEFAULT_STRATEGY_REGISTRY
from roundup_crypto_lab.dca_pilots import build_pilot_strategy, registered_pilots
from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.dca_strategy import (
    CausalIndicator,
    PendingCashBucket,
    build_decision_context,
    evaluate_strategy,
)
from roundup_crypto_lab.deployment_engine import (
    build_result,
    load_kraken_candles,
    parse_timerange,
    purchase,
)
from roundup_crypto_lab.investment_plan import (
    CashFlowEvent,
    InvestmentPlan,
    contribution_schedule,
)
from roundup_crypto_lab.scenario_passive import run_scenario_passive

SCHEMA_VERSION = "dca-controlled-comparison/v1"
MANIFEST_VERSION = "dca-controlled-comparison-manifest/v1"


def _decimal(value: object) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("numeric values must be finite")
    return number


def _canonical(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value, "f").rstrip("0").rstrip(".")


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _schedule(
    plan: InvestmentPlan,
    start: datetime,
    end: datetime,
) -> tuple[CashFlowEvent, ...]:
    return contribution_schedule(plan, start, end)


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    work["previous_close"] = close.shift(1)
    work["long_ma"] = close.shift(1).rolling(200, min_periods=200).mean()
    work["rolling_high"] = high.shift(1).rolling(180, min_periods=1).max()
    work["rolling_drawdown"] = (
        (work["rolling_high"] - close.shift(1)) / work["rolling_high"]
    ).clip(lower=0)
    direction = (close.shift(1) - close.shift(21)).abs()
    distance = close.shift(1).diff().abs().rolling(20, min_periods=20).sum()
    work["ker_20"] = direction / distance.replace(0, float("nan"))
    up = high.shift(1).diff()
    down = -low.shift(1).diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prior_close = close.shift(2)
    true_range = pd.concat(
        [
            (high.shift(1) - low.shift(1)).abs(),
            (high.shift(1) - prior_close).abs(),
            (low.shift(1) - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14, min_periods=14).sum()
    plus_di = 100 * plus_dm.rolling(14, min_periods=14).sum() / atr
    minus_di = 100 * minus_dm.rolling(14, min_periods=14).sum() / atr
    denominator = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / denominator
    work["adx_14"] = dx.rolling(14, min_periods=14).mean()
    return work


def _indicator_values(
    definition: Any,
    row: Any,
    observed_at: datetime,
) -> tuple[CausalIndicator, ...] | None:
    values = []
    for requirement in definition.required_indicators:
        value = getattr(row, requirement.name)
        if pd.isna(value):
            return None
        values.append(
            CausalIndicator(
                requirement.name,
                Decimal(str(value)),
                observed_at,
            )
        )
    return tuple(sorted(values, key=lambda item: item.name))


def _consume_fifo(
    pending: list[list[Any]],
    amount: Decimal,
) -> list[dict[str, str]]:
    remaining = amount
    consumed = []
    for bucket in pending:
        if remaining <= 0:
            break
        take = min(bucket[1], remaining)
        if take > 0:
            bucket[1] -= take
            remaining -= take
            consumed.append(
                {
                    "contributed_at": bucket[0].isoformat(),
                    "amount": _canonical(take),
                }
            )
    if remaining != 0:
        raise ValueError("pilot execution exceeds available contributed cash")
    pending[:] = [bucket for bucket in pending if bucket[1] > 0]
    return consumed


def run_pilot(
    *,
    definition: Any,
    candles: pd.DataFrame,
    events: tuple[CashFlowEvent, ...],
    plan: InvestmentPlan,
    pair: str,
    period_end: datetime,
) -> dict[str, Any]:
    strategy = build_pilot_strategy(definition)
    enriched = _indicators(candles)
    state: Mapping[str, Any] = {}
    pending: list[list[Any]] = []
    purchases: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    event_position = 0
    quantity = Decimal("0")
    fees = Decimal("0")

    for row in enriched.itertuples(index=False):
        decision_at = row.date.to_pydatetime().astimezone(UTC)
        while (
            event_position < len(events)
            and events[event_position].contributed_at <= decision_at
        ):
            event = events[event_position]
            pending.append([event.contributed_at, event.amount])
            event_position += 1
        available = sum((bucket[1] for bucket in pending), Decimal("0"))
        if available <= 0:
            continue
        indicators = _indicator_values(definition, row, decision_at)
        if indicators is None:
            continue
        mark = (
            Decimal(str(row.previous_close))
            if not pd.isna(row.previous_close)
            else Decimal("0")
        )
        cumulative_contributions = sum(
            (
                event.amount
                for event in events
                if event.contributed_at <= decision_at
            ),
            Decimal("0"),
        )
        context = build_decision_context(
            decision_at=decision_at,
            available_cash=available,
            quantity=quantity,
            marked_asset_value=quantity * mark,
            cumulative_contributions=cumulative_contributions,
            cumulative_fees=fees,
            pending_cash=tuple(
                PendingCashBucket(bucket[0], bucket[1]) for bucket in pending
            ),
            candles=candles,
            indicators=indicators,
            state=state,
        )
        decision = evaluate_strategy(strategy, context)
        requested = sum(
            (order.gross_amount for order in decision.orders),
            Decimal("0"),
        )
        execution_rows = []
        for order in decision.orders:
            source = CashFlowEvent(pending[0][0], available, "strategy")
            execution = purchase(
                candles,
                source,
                decision_at,
                order.gross_amount,
                plan.fee_ratio,
            )
            if execution is None:
                continue
            execution["decision_tag"] = decision.decision_tag
            execution["order_tag"] = order.order_tag
            execution["funding_allocations"] = _consume_fifo(
                pending,
                order.gross_amount,
            )
            purchases.append(execution)
            quantity += execution["quantity"]
            fees += execution["fee_paid"]
            execution_rows.append(execution)
        executed = sum(
            (_decimal(item["gross_contribution"]) for item in execution_rows),
            Decimal("0"),
        )
        decisions.append(
            {
                "strategy_id": definition.strategy_id,
                "strategy_version": definition.strategy_version,
                "decision_at": decision_at.isoformat(),
                "decision_tag": decision.decision_tag,
                "available_cash_before": _canonical(available),
                "requested_gross_amount": _canonical(requested),
                "executed_gross_amount": _canonical(executed),
                "cash_after": _canonical(
                    sum((bucket[1] for bucket in pending), Decimal("0"))
                ),
                "indicator_values": {
                    item.name: _canonical(item.value) for item in indicators
                },
                "diagnostics": json.loads(
                    json.dumps(decision.diagnostics, default=str, sort_keys=True)
                ),
                "state_before": json.loads(
                    json.dumps(state, default=str, sort_keys=True)
                ),
                "state_after": json.loads(
                    json.dumps(decision.next_state, default=str, sort_keys=True)
                ),
            }
        )
        state = decision.next_state

    result = build_result(
        definition.strategy_id,
        pair,
        candles,
        events,
        purchases,
    )
    result["strategy"] = {
        "strategy_id": definition.strategy_id,
        "strategy_version": definition.strategy_version,
        "implementation": definition.implementation,
        "parameters": dict(definition.parameters),
    }
    result["decision_ledger"] = decisions
    result["deployment_metrics"] = {
        "decision_count": len(decisions),
        "buy_count": len(purchases),
        "final_uninvested_cash": result["cash_balance_exact"],
        "oldest_retained_cash_age_seconds": max(
            (
                int((period_end - bucket[0]).total_seconds())
                for bucket in pending
            ),
            default=0,
        ),
    }
    return result


def _comparison_row(
    result: Mapping[str, Any],
    total_contributions: Decimal,
) -> dict[str, Any]:
    metrics = result.get("cash_flow_metrics", {})
    deployment = result.get(
        "dca_metrics",
        result.get("deployment_metrics", {}),
    )
    quantity = _decimal(result["quantity_exact"])
    method = result.get(
        "benchmark",
        result.get("strategy", {}).get("strategy_id"),
    )
    return {
        "method": method,
        "final_value": result["final_value_exact"],
        "profit": result["profit_total_abs"],
        "xirr": metrics.get("xirr"),
        "final_crypto_quantity": result["quantity_exact"],
        "quantity_per_contribution": _canonical(
            quantity / total_contributions
        ),
        "capital_deployment_ratio": deployment.get(
            "time_weighted_capital_deployment_ratio"
        ),
        "maximum_purchase_delay_seconds": deployment.get(
            "maximum_contribution_to_purchase_delay_seconds"
        ),
        "oldest_retained_cash_age_seconds": deployment.get(
            "oldest_uninvested_cash_age_seconds",
            deployment.get("oldest_retained_cash_age_seconds"),
        ),
        "fees": str(result["fees_paid"]),
        "action_count": result["number_of_buys"],
        "twr": metrics.get("twr"),
        "raw_drawdown": result.get("max_drawdown_raw_portfolio"),
        "contribution_neutral_drawdown": metrics.get(
            "max_drawdown_time_weighted"
        ),
        "final_uninvested_cash": result["cash_balance_exact"],
    }


def run_comparison(
    *,
    data_dir: Path,
    pair: str,
    timeframe: str,
    timerange: str,
    registry_path: Path,
    initial_capital: str,
    monthly_budget: str,
    contribution_day: int,
    fee: str,
    repository_commit: str,
) -> dict[str, Any]:
    start, end = parse_timerange(timerange)
    plan = InvestmentPlan(
        initial_capital,
        monthly_budget,
        fee,
        contribution_day,
    )
    events = _schedule(plan, start, end)
    registry = load_registry(registry_path)
    candles = load_kraken_candles(
        data_dir,
        pair,
        timeframe,
        timerange,
    )
    passive = run_scenario_passive(
        data_dir=data_dir,
        pair=pair,
        timeframe=timeframe,
        timerange=timerange,
        capital_mode="recurring_monthly_contributions",
        initial_capital=initial_capital,
        monthly_budget=monthly_budget,
        contribution_day=contribution_day,
        fee=fee,
        repository_commit=repository_commit,
        registry_path=registry_path,
    )
    pilots = [
        run_pilot(
            definition=definition,
            candles=candles,
            events=events,
            plan=plan,
            pair=pair,
            period_end=end,
        )
        for definition in registered_pilots(registry)
    ]
    expected_schedule = passive["metadata"]["contribution_schedule"]
    contributions = [
        {
            "timestamp": row["contributed_at"],
            "amount": row["amount"],
        }
        for row in expected_schedule
    ]
    for pilot in pilots:
        snapshots = [
            {
                "timestamp": row["timestamp"],
                "equity": Decimal(str(row["cash_balance"]))
                + Decimal(str(row["crypto_value"])),
                "cash": row["cash_balance"],
                "asset_value": row["crypto_value"],
                "share_value": row["time_weighted_share_value"],
            }
            for row in pilot["equity_curve"]
        ]
        pilot["cash_flow_metrics"] = build_cash_flow_metrics(
            initial_capital=plan.initial_capital,
            monthly_budget=plan.monthly_budget,
            fee_ratio=plan.fee_ratio,
            contributions=contributions,
            snapshots=snapshots,
            total_fees=pilot["fees_paid"],
            period_end=end,
        )
    total = sum((event.amount for event in events), Decimal("0"))
    all_results = [*passive["benchmarks"], *pilots]
    rows = [_comparison_row(item, total) for item in all_results]
    methods = [row["method"] for row in rows]
    if len(methods) != len(set(methods)):
        raise ValueError(
            "controlled comparison contains duplicate strategy identities"
        )
    for result in all_results:
        if result["total_contributions"] != passive["metadata"]["total_contributions"]:
            raise ValueError(
                "controlled comparison contribution totals are incompatible"
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario": {
            "pair": pair,
            "timeframe": timeframe,
            "timerange": timerange,
            "initial_capital": initial_capital,
            "monthly_budget": monthly_budget,
            "contribution_day": contribution_day,
            "fee_ratio": fee,
            "contribution_schedule": expected_schedule,
            "total_contributions": _canonical(total),
        },
        "registry": {
            "registry_id": registry.registry_id,
            "registry_digest": registry.digest,
        },
        "results": {
            "baselines": passive["benchmarks"],
            "pilots": pilots,
        },
        "comparison": rows,
    }


def write_outputs(
    payload: Mapping[str, Any],
    output_dir: Path,
    *,
    registry_path: Path,
    repository_commit: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_json = output_dir / "controlled-comparison.json"
    comparison_json.write_text(
        json.dumps(
            payload,
            indent=2,
            allow_nan=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    rows = payload["comparison"]
    with (output_dir / "controlled-comparison.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    details = output_dir / "strategies"
    details.mkdir(exist_ok=True)
    results = [
        *payload["results"]["baselines"],
        *payload["results"]["pilots"],
    ]
    for result in results:
        raw_name = result.get(
            "benchmark",
            result.get("strategy", {}).get("strategy_id"),
        )
        name = str(raw_name).lower().replace("_", "-")
        (details / f"{name}.json").write_text(
            json.dumps(
                result,
                indent=2,
                allow_nan=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        for key, suffix in (
            ("decision_ledger", "decision-ledger"),
            ("purchase_ledger", "purchase-ledger"),
        ):
            ledger = result.get(key, [])
            if not ledger:
                continue
            fields = sorted({field for row in ledger for field in row})
            with (details / f"{name}-{suffix}.csv").open(
                "w",
                newline="",
                encoding="utf-8",
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=fields,
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(
                    {
                        key: (
                            json.dumps(value, sort_keys=True, default=str)
                            if isinstance(value, (dict, list))
                            else value
                        )
                        for key, value in row.items()
                    }
                    for row in ledger
                )
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "repository_commit": repository_commit,
        "registry_digest": payload["registry"]["registry_digest"],
        "registry_file_digest": _digest(registry_path),
        "scenario": payload["scenario"],
        "python_version": platform.python_version(),
        "pandas_version": pd.__version__,
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    (output_dir / "reproducibility-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Controlled DCA strategy comparison",
        "",
        f"Pair: `{payload['scenario']['pair']}`",
        f"Registry: `{payload['registry']['registry_digest']}`",
        "",
        "| Method | Final value | Final quantity | Cash | Actions |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['method']} | {row['final_value']} | "
            f"{row['final_crypto_quantity']} | "
            f"{row['final_uninvested_cash']} | {row['action_count']} |"
        )
    lines.extend(
        [
            "",
            "Rankings are intentionally separate; this workflow does not "
            "compute a composite winner.",
            "",
        ]
    )
    (output_dir / "job-summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("user_data/data/kraken"),
    )
    parser.add_argument(
        "--pair",
        required=True,
        choices=("BTC/EUR", "ETH/EUR"),
    )
    parser.add_argument(
        "--timeframe",
        required=True,
        choices=("4h",),
    )
    parser.add_argument("--timerange", required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_STRATEGY_REGISTRY,
    )
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--fee", required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    payload = run_comparison(
        data_dir=args.data_dir,
        pair=args.pair,
        timeframe=args.timeframe,
        timerange=args.timerange,
        registry_path=args.registry,
        initial_capital=args.initial_capital,
        monthly_budget=args.monthly_budget,
        contribution_day=args.contribution_day,
        fee=args.fee,
        repository_commit=args.repository_commit,
    )
    write_outputs(
        payload,
        args.output_dir,
        registry_path=args.registry,
        repository_commit=args.repository_commit,
    )


if __name__ == "__main__":
    main()
