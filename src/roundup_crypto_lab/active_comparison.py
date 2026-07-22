"""Versioned, validated active-cash-flow artifacts; intentionally separate from native metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER, validate_comparison
from roundup_crypto_lab.passive_benchmarks import parse_timerange

SCHEMA_VERSION = "active-strategy-result/v1"
EXITS = frozenset({"exit_signal", "stop_loss"})


def dec(x: object, n: str) -> Decimal:
    try:
        v = Decimal(str(x))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"{n} must be decimal") from e
    if not v.is_finite():
        raise ValueError(f"{n} must be finite")
    return v


def ts(x: object, n: str) -> datetime:
    try:
        v = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"{n} must be ISO timestamp") from e
    if v.tzinfo is None:
        raise ValueError(f"{n} must be timezone-aware")
    return v.astimezone(UTC)


def identity(e: dict[str, object]) -> str:
    keys = (
        "selected_pair",
        "timeframe",
        "timerange",
        "start",
        "end",
        "capital_mode",
        "investment_plan",
        "effective_settings",
        "execution_model",
        "execution_scope",
    )
    return hashlib.sha256(
        json.dumps(
            {k: e.get(k) for k in keys}, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


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
    start, end = parse_timerange(timerange)
    trades = result.get("trades")
    curve = result.get("equity_curve")
    if (
        not isinstance(trades, list)
        or not isinstance(curve, list)
        or not curve
        or not isinstance(curve[-1], dict)
    ):
        raise ValueError("adapter result lacks ledger")
    exits: dict[str, int] = {}
    for t in trades:
        if not isinstance(t, dict):
            raise ValueError("trade must be object")
        if t.get("exit_reason"):
            exits[str(t["exit_reason"])] = exits.get(str(t["exit_reason"]), 0) + 1
    e = {
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
    e["experiment_id"] = identity(e)
    final = curve[-1]
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": e,
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
        "known_limitations": ["Recurring mode is not native Freqtrade-equivalent."],
    }


def validate_active_result(p: dict[str, object], **expected: object) -> None:
    if p.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema")
    e = p.get("experiment")
    m = p.get("adapter_metrics")
    schedule = p.get("contribution_schedule")
    ledger = p.get("contribution_ledger")
    trades = p.get("trade_ledger")
    curve = p.get("equity_curve")
    if (
        not all(isinstance(x, dict) for x in (e, m))
        or not all(isinstance(x, list) for x in (schedule, ledger, trades, curve))
        or not curve
    ):
        raise ValueError("missing artifact fields")
    e = e
    m = m
    schedule = schedule
    ledger = ledger
    trades = trades
    curve = curve
    for key, val in expected.items():
        actual = e.get(key.replace("pair", "selected_pair"))
        if val is not None and actual != val:
            raise ValueError(f"unexpected {key}")
    start, end = ts(e.get("start"), "start"), ts(e.get("end"), "end")
    if (
        start >= end
        or parse_timerange(str(e.get("timerange"))) != (start, end)
        or e.get("experiment_id") != identity(e)
    ):
        raise ValueError("inconsistent experiment identity")
    if not isinstance(e.get("execution_scope"), dict) or not e["execution_scope"].get(
        "config_digest"
    ):
        raise ValueError("missing execution scope digest")

    def ordered(rows: list[object], key: str, extra: str | None = None) -> None:
        prev = start
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("ledger row must object")
            v = ts(row.get(key), key)
            if not start <= v < end or v < prev:
                raise ValueError("ledger is not chronological")
            if extra and ts(row.get(extra), extra) < v:
                raise ValueError("contribution credited before scheduled")
            prev = v

    ordered(schedule, "contributed_at")
    ordered(ledger, "investor_contribution_timestamp", "credited_at")
    ordered(trades, "entry_timestamp")
    ordered(curve, "timestamp")
    if len(schedule) != len(ledger):
        raise ValueError("schedule/ledger length differs")
    total = Decimal()
    fees = Decimal()
    deployed = Decimal()
    exits: dict[str, int] = {}
    open_trades = []
    previous_exit = start
    for scheduled, credited in zip(schedule, ledger, strict=True):
        if (
            not isinstance(scheduled, dict)
            or not isinstance(credited, dict)
            or dec(scheduled.get("amount"), "amount") != dec(credited.get("amount"), "amount")
            or ts(scheduled.get("contributed_at"), "date")
            != ts(credited.get("investor_contribution_timestamp"), "date")
        ):
            raise ValueError("contribution ledger differs from schedule")
        total += dec(scheduled["amount"], "amount")
    for t in trades:
        if not isinstance(t, dict):
            raise ValueError("trade invalid")
        stake, entryfee = (
            dec(t.get("entry_gross_stake"), "stake"),
            dec(t.get("entry_fee"), "entry fee"),
        )
        if stake + entryfee > dec(t.get("cash_available"), "cash available"):
            raise ValueError("buy exceeds cash")
        fees += entryfee
        entry = ts(t["entry_timestamp"], "entry")
        if entry < previous_exit:
            raise ValueError("overlapping trades")
        if t.get("exit_reason") is None:
            if any(
                t.get(x) is not None
                for x in ("exit_timestamp", "exit_price", "exit_fee", "net_proceeds", "total_fees")
            ):
                raise ValueError("open trade has exit fields")
            open_trades.append(t)
            deployed = stake
            continue
        if t["exit_reason"] not in EXITS:
            raise ValueError("unsupported exit")
        required = ("exit_timestamp", "exit_price", "exit_fee", "net_proceeds", "total_fees")
        if any(t.get(x) is None for x in required):
            raise ValueError("closed trade missing fields")
        exit = ts(t["exit_timestamp"], "exit")
        if exit < entry or exit >= end:
            raise ValueError("invalid exit timestamp")
        exitfee = dec(t["exit_fee"], "exit fee")
        totalfees = dec(t["total_fees"], "total fees")
        if exitfee < 0 or totalfees != entryfee + exitfee:
            raise ValueError("inconsistent fees")
        fees += exitfee
        exits[str(t["exit_reason"])] = exits.get(str(t["exit_reason"]), 0) + 1
        previous_exit = exit
    if len(open_trades) > 1:
        raise ValueError("more than one open trade")
    previous_contributed = Decimal()
    previous_gross = Decimal()
    for row in curve:
        if not isinstance(row, dict):
            raise ValueError("equity row invalid")
        for key in (
            "mark_price",
            "free_cash",
            "crypto_value",
            "current_deployed_capital",
            "cumulative_gross_deployed",
            "equity",
            "cumulative_contributions",
            "investment_return",
            "time_weighted_share_value",
        ):
            value = dec(row.get(key), key)
            if key == "time_weighted_share_value" and value <= 0:
                raise ValueError("share value must be positive")
            if (
                key
                in {
                    "mark_price",
                    "free_cash",
                    "crypto_value",
                    "current_deployed_capital",
                    "cumulative_gross_deployed",
                    "cumulative_contributions",
                }
                and value < 0
            ):
                raise ValueError(f"negative {key}")
        if (
            dec(row["mark_price"], "mark") <= 0
            or dec(row["equity"], "equity")
            != dec(row["free_cash"], "cash") + dec(row["crypto_value"], "crypto")
            or dec(row["investment_return"], "return")
            != dec(row["equity"], "equity") - dec(row["cumulative_contributions"], "contributed")
        ):
            raise ValueError("invalid equity row")
        if (
            dec(row["cumulative_contributions"], "contributed") < previous_contributed
            or dec(row["cumulative_gross_deployed"], "gross") < previous_gross
        ):
            raise ValueError("decreasing cumulative curve")
        previous_contributed, previous_gross = (
            dec(row["cumulative_contributions"], "contributed"),
            dec(row["cumulative_gross_deployed"], "gross"),
        )
    shares = [
        dec(row["time_weighted_share_value"], "share") for row in curve if isinstance(row, dict)
    ]
    peak = shares[0]
    drawdown = Decimal()
    for share in shares:
        peak = max(peak, share)
        drawdown = max(drawdown, (peak - share) / peak)
    if (
        dec(m["contribution_neutral_return"], "neutral return") != shares[-1] - 1
        or dec(m["contribution_neutral_max_drawdown"], "neutral drawdown") != drawdown
        or not Decimal() <= drawdown <= Decimal(1)
    ):
        raise ValueError("contribution-neutral metrics mismatch")
    last = curve[-1]
    if not isinstance(last, dict):
        raise ValueError("equity invalid")
    for k in (
        "free_cash",
        "crypto_value",
        "current_deployed_capital",
        "cumulative_gross_deployed",
        "equity",
        "cumulative_contributions",
        "investment_return",
    ):
        dec(last.get(k), k)
    for k in (
        "total_contributed_capital",
        "free_cash",
        "crypto_value",
        "current_deployed_capital",
        "cumulative_gross_deployed",
        "final_equity",
        "fees_paid",
    ):
        if dec(m.get(k), k) < 0:
            raise ValueError(f"negative {k}")
    if (
        dec(last["equity"], "equity")
        != dec(last["free_cash"], "cash") + dec(last["crypto_value"], "crypto")
        or total != dec(m["total_contributed_capital"], "total")
        or total != dec(last["cumulative_contributions"], "curve total")
    ):
        raise ValueError("equity or contributions mismatch")
    pairs = {
        "free_cash": "free_cash",
        "crypto_value": "crypto_value",
        "current_deployed_capital": "current_deployed_capital",
        "cumulative_gross_deployed": "cumulative_gross_deployed",
        "final_equity": "equity",
        "investment_return": "investment_return",
    }
    if (
        any(dec(m[a], a) != dec(last[b], b) for a, b in pairs.items())
        or dec(m["investment_return"], "return") != dec(m["final_equity"], "equity") - total
    ):
        raise ValueError("final metrics mismatch")
    if (
        m.get("entry_count") != len(trades)
        or m.get("exit_count") != sum(exits.values())
        or m.get("exit_reason_counts") != exits
        or dec(m["fees_paid"], "fees") != fees
    ):
        raise ValueError("summary counts or fees mismatch")
    state = m.get("open_position_state")
    if (
        state not in {"closed", "open_marked_at_final_close"}
        or (bool(open_trades) != (state == "open_marked_at_final_close"))
        or dec(m["current_deployed_capital"], "deployed") != deployed
    ):
        raise ValueError("position state mismatch")
    if open_trades and dec(m["crypto_value"], "crypto") != dec(
        open_trades[0]["quantity"], "quantity"
    ) * dec(last["mark_price"], "mark price"):
        raise ValueError("crypto value mismatch")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--active-result", action="append", type=Path, required=True)
    ap.add_argument("--native-comparison", type=Path, required=True)
    ap.add_argument("--metadata", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--one-shot-differential", type=Path)
    a = ap.parse_args()
    results = [json.loads(x.read_text()) for x in a.active_result]
    if len(results) != 7:
        raise ValueError("exactly seven active results required")
    for r in results:
        validate_active_result(r)
    identities = {
        r["experiment"]["experiment_id"] for r in results if isinstance(r.get("experiment"), dict)
    }
    if len(identities) != 1 or {r["experiment"]["strategy"] for r in results} != {*STRATEGY_ORDER}:
        raise ValueError("results do not share one complete experiment")
    native = validate_comparison(a.native_comparison)
    meta = json.loads(a.metadata.read_text())
    exp = results[0]["experiment"]
    if (
        not isinstance(exp, dict)
        or meta.get("timerange") != exp["timerange"]
        or meta.get("timeframe") != exp["timeframe"]
        or meta.get("pairs") != exp["selected_pair"]
    ):
        raise ValueError("native metadata differs from active experiment")
    differential = None
    if exp["capital_mode"] == "one_shot_capital" and not a.one_shot_differential:
        raise ValueError("one-shot comparison requires differential artifact")
    if exp["capital_mode"] != "one_shot_capital" and a.one_shot_differential:
        raise ValueError("recurring comparison must not include differential artifact")
    if a.one_shot_differential:
        differential = json.loads(a.one_shot_differential.read_text())
        expected = ("experiment_id", "selected_pair", "timeframe", "timerange", "capital_mode")
        if differential.get("schema_version") != "one-shot-differential/v1" or any(
            differential.get(k) != exp.get(k) for k in expected
        ):
            raise ValueError("invalid one-shot differential artifact")
        rows = differential.get("strategies")
        if (
            not isinstance(rows, list)
            or [row.get("strategy") for row in rows if isinstance(row, dict)]
            != list(STRATEGY_ORDER)
            or any(not isinstance(row, dict) or row.get("status") != "passed" for row in rows)
        ):
            raise ValueError("invalid one-shot differential strategies")
    out = {
        "schema_version": "controlled-comparison/v1",
        "experiment": exp,
        "native_freqtrade_one_shot_reference": native,
        "active_investor_cash_flow_simulation": results,
        **({"one_shot_differential": differential} if differential else {}),
    }
    a.output.write_text(json.dumps(out, default=str, indent=2) + "\n")
    lines = (
        [
            "# Native Freqtrade one-shot reference",
            "",
            "| Strategy | Trades | Profit total % | Profit abs | Win rate % | Max drawdown % | Profit factor | Expectancy |",  # noqa: E501
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        + [
            "| {strategy} | {trades} | {profit_total:.2%} | {profit_total_abs:.8f} | {winrate:.2%} | {max_drawdown_account:.2%} | {profit_factor:.4f} | {expectancy:.8f} |".format(  # noqa: E501
                **x
            )
            for x in native
        ]
        + ["", "# Active investor cash-flow simulation", ""]
    )
    for r in results:
        mm = r["adapter_metrics"]
        ee = r["experiment"]
        lines.append(
            f"- {ee['strategy']}: contributed {mm['total_contributed_capital']}; equity {mm['final_equity']}; return {mm['investment_return']}; cash {mm['free_cash']}; crypto {mm['crypto_value']}; neutral return {mm['contribution_neutral_return']}; neutral drawdown {mm['contribution_neutral_max_drawdown']}; entries {mm['entry_count']}; exits {mm['exit_count']}; stop exits {mm['exit_reason_counts'].get('stop_loss', 0)}; position {mm['open_position_state']}."  # noqa: E501
        )
    lines += [
        "",
        "Recurring results are ranked only by contribution-neutral metrics under this identical experiment."  # noqa: E501
        if exp["capital_mode"] == "recurring_monthly_contributions"
        else "One-shot differential validation executed successfully; lifecycle and final balances passed.",  # noqa: E501
    ]
    if differential:
        lines += [
            "",
            "# One-shot differential validation",
            "",
            f"Experiment ID: `{differential['experiment_id']}`",
            "",
            "| Strategy | Status | Trades checked | Lifecycle | Final balances |",
            "| --- | --- | ---: | --- | --- |",
        ] + [
            f"| {row['strategy']} | {row['status']} | {row['trade_count']} | passed | passed |"
            for row in differential["strategies"]
        ]
    a.summary.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
