"""Plan, execute and aggregate the frozen short-delay DCA research campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from calendar import monthrange
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.deployment_engine import load_kraken_candles, parse_timerange
from roundup_crypto_lab.execution_costs import resolve_cost_profile
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule
from roundup_crypto_lab.short_delay_dca import (
    MAXIMUM_DELAY_DAYS,
    load_and_validate_protocol,
)
from roundup_crypto_lab.short_delay_execution import execute_short_delay_strategy

SCHEMA_VERSION = "short-delay-dca-campaign/v1"
SCENARIO_SCHEMA_VERSION = "short-delay-dca-scenario/v1"
STATUS_SCHEMA_VERSION = "short-delay-dca-campaign-status/v1"
SIGNAL_WARMUP_DAYS = 14
STRATEGIES = (
    "monthly_dca_control",
    "negative_7d_return_delay",
    "below_7d_sma_delay",
    "confirmed_short_decline_delay",
)
PROFILES = (
    "frictionless-control-v1",
    "proportional-fee-v1",
    "proportional-plus-spread-v1",
    "hypothetical-fixed-cost-v1",
)
SECTIONS = ("multi-window", "historical-complement")
EXPECTED_COUNTS = {
    "multi-window": 48,
    "historical-complement": 4,
    "all": 52,
    "strategies_per_scenario": 4,
    "all_strategy_results": 208,
}
EXPECTED_WINDOWS = {
    "rolling-24m-6m-step": (
        "multi-window",
        "20180701",
        "20240101",
        24,
        6,
        True,
    ),
    "non-overlapping-24m": (
        "multi-window",
        "20200101",
        "20240101",
        24,
        24,
        False,
    ),
    "rolling-48m-12m-step": (
        "multi-window",
        "20190101",
        "20240101",
        48,
        12,
        True,
    ),
    "continuous-long-horizon": (
        "historical-complement",
        "20180701",
        "20260101",
        90,
        90,
        False,
    ),
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _month_add(value: datetime, months: int) -> datetime:
    position = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(position, 12)
    month = month_index + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _day(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must use YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYYMMDD") from exc


def load_campaign(path: Path) -> dict[str, Any]:
    """Load and strictly validate the committed campaign definition."""

    payload = _load_json(path)
    expected = {
        "schema_version",
        "campaign_id",
        "protocol_path",
        "cost_profile_dir",
        "pair",
        "timeframe",
        "investment_plan",
        "cost_profiles",
        "strategy_ids",
        "window_sets",
        "known_excluded_gap",
        "disclosures",
        "expected_counts",
    }
    if set(payload) != expected:
        raise ValueError("short-delay campaign keys drifted")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"campaign schema must be {SCHEMA_VERSION}")
    if payload["pair"] != "BTC/EUR" or payload["timeframe"] != "4h":
        raise ValueError("campaign market contract must remain BTC/EUR 4h")
    if tuple(payload["strategy_ids"]) != STRATEGIES:
        raise ValueError("campaign must contain exactly the four frozen strategies")
    if tuple(payload["cost_profiles"]) != PROFILES:
        raise ValueError("campaign must contain exactly the four committed cost profiles")
    if payload["expected_counts"] != EXPECTED_COUNTS:
        raise ValueError("campaign expected counts drifted")
    plan = payload["investment_plan"]
    expected_plan = {
        "initial_capital": "40",
        "monthly_budget": "40",
        "contribution_day": 1,
    }
    if plan != expected_plan:
        raise ValueError("campaign contribution assumptions drifted")
    expected_gap = {
        "start": "2018-01-11T20:00:00+00:00",
        "end": "2018-01-13T08:00:00+00:00",
        "duration_hours": 36,
        "policy": "reject_affected_windows_without_imputation",
    }
    if payload["known_excluded_gap"] != expected_gap:
        raise ValueError("known Kraken gap contract drifted")
    windows = payload["window_sets"]
    if not isinstance(windows, list) or len(windows) != len(EXPECTED_WINDOWS):
        raise ValueError("campaign must define exactly four window sets")
    actual_windows: dict[str, tuple[object, ...]] = {}
    for index, row in enumerate(windows):
        required = {
            "window_set_id",
            "research_section",
            "start",
            "end",
            "months",
            "step_months",
            "overlapping",
        }
        if not isinstance(row, Mapping) or set(row) != required:
            raise ValueError(f"window_sets[{index}] keys drifted")
        identity = str(row["window_set_id"])
        actual_windows[identity] = (
            row["research_section"],
            row["start"],
            row["end"],
            row["months"],
            row["step_months"],
            row["overlapping"],
        )
    if actual_windows != EXPECTED_WINDOWS:
        raise ValueError("research window structure drifted")
    return payload


def campaign_provenance(campaign: Mapping[str, Any]) -> dict[str, Any]:
    protocol_path = Path(str(campaign["protocol_path"]))
    protocol = load_and_validate_protocol(protocol_path)
    strategies = protocol["strategies"]
    if tuple(row["strategy_id"] for row in strategies) != STRATEGIES:
        raise ValueError("protocol strategy order differs from campaign")
    return {
        "campaign_digest": _digest(campaign),
        "protocol_digest": _digest(protocol),
        "strategy_registry_digest": _digest(strategies),
        "protocol_id": protocol["protocol_id"],
        "visible_data_convention": protocol["observation_contract"],
        "maximum_delay_calendar_days": MAXIMUM_DELAY_DAYS,
        "signal_warmup_days": SIGNAL_WARMUP_DAYS,
    }


def plan_campaign(
    campaign: Mapping[str, Any],
    section: str,
) -> list[dict[str, Any]]:
    """Materialize the exact deterministic scenario matrix for one section."""

    if section not in SECTIONS:
        raise ValueError(f"section must be one of: {', '.join(SECTIONS)}")
    rows: list[dict[str, Any]] = []
    for window in campaign["window_sets"]:
        if window["research_section"] != section:
            continue
        current = _day(window["start"], "window start")
        boundary = _day(window["end"], "window end")
        while _month_add(current, int(window["months"])) <= boundary:
            end = _month_add(current, int(window["months"]))
            timerange = f"{current:%Y%m%d}-{end:%Y%m%d}"
            for profile_id in campaign["cost_profiles"]:
                scenario_id = "::".join(
                    (
                        section,
                        str(profile_id),
                        str(window["window_set_id"]),
                        timerange,
                    )
                )
                rows.append(
                    {
                        "scenario_id": scenario_id,
                        "campaign_id": campaign["campaign_id"],
                        "research_section": section,
                        "window_set_id": window["window_set_id"],
                        "timerange": timerange,
                        "pair": campaign["pair"],
                        "timeframe": campaign["timeframe"],
                        "cost_profile_id": profile_id,
                        "cost_profile_dir": campaign["cost_profile_dir"],
                        "protocol_path": campaign["protocol_path"],
                        **deepcopy(campaign["investment_plan"]),
                    }
                )
            current = _month_add(current, int(window["step_months"]))
    rows.sort(key=lambda row: row["scenario_id"])
    expected = int(campaign["expected_counts"][section])
    if len(rows) != expected:
        raise ValueError(f"unexpected {section} scenario count: {len(rows)}")
    if len({row["scenario_id"] for row in rows}) != len(rows):
        raise ValueError("campaign produced duplicate scenario identities")
    return rows


def _serialize(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _signal_timerange(timerange: str) -> str:
    start, end = parse_timerange(timerange)
    signal_start = start - timedelta(days=SIGNAL_WARMUP_DAYS)
    return f"{signal_start:%Y%m%d}-{end:%Y%m%d}"


def run_scenario(
    *,
    campaign: Mapping[str, Any],
    scenario: Mapping[str, Any],
    data_dir: Path,
    repository_commit: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Execute all four frozen strategies for one window and cost profile."""

    timerange = str(scenario["timerange"])
    start, end = parse_timerange(timerange)
    provenance = campaign_provenance(campaign)
    profile = resolve_cost_profile(
        str(scenario["cost_profile_id"]),
        search_dir=Path(str(scenario["cost_profile_dir"])),
    )
    plan = InvestmentPlan(
        str(scenario["initial_capital"]),
        str(scenario["monthly_budget"]),
        profile.trading_fee_ratio,
        int(scenario["contribution_day"]),
    )
    events = contribution_schedule(plan, start, end)
    signal_timerange = _signal_timerange(timerange)
    candles = load_kraken_candles(
        data_dir,
        str(scenario["pair"]),
        str(scenario["timeframe"]),
        signal_timerange,
    )
    results = [
        execute_short_delay_strategy(
            strategy_id=strategy_id,
            events=events,
            candles=candles,
            pair=str(scenario["pair"]),
            profile=profile,
        )
        for strategy_id in STRATEGIES
    ]
    payload = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "scenario": {
            **dict(scenario),
            "signal_data_timerange": signal_timerange,
        },
        "repository_commit": repository_commit,
        "provenance": {
            **provenance,
            "cost_profile_id": profile.cost_profile_id,
            "cost_profile_digest": profile.digest,
        },
        "contribution_assumptions": campaign["investment_plan"],
        "results": results,
        "disclosures": campaign["disclosures"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    serialized = _serialize(payload)
    (output_dir / "short-delay-scenario.json").write_text(
        json.dumps(serialized, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    for result in serialized["results"]:
        strategy_id = result["strategy"]["strategy_id"]
        strategy_dir = output_dir / "strategies" / strategy_id
        strategy_dir.mkdir(parents=True, exist_ok=True)
        (strategy_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (strategy_dir / "signal-ledger.json").write_text(
            json.dumps(result["signal_ledger"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (strategy_dir / "contribution-ledger.json").write_text(
            json.dumps(result["funding_allocations"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return serialized


def aggregate_campaign(
    *,
    campaign: Mapping[str, Any],
    section: str,
    result_files: Sequence[Path],
    repository_commit: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Validate complete coverage before producing comparisons or rankings."""

    planned = plan_campaign(campaign, section)
    expected_ids = {row["scenario_id"] for row in planned}
    scenarios = [_load_json(path) for path in sorted(result_files)]
    actual_ids = {row["scenario"]["scenario_id"] for row in scenarios}
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    invalid = [
        row
        for row in scenarios
        if len(row.get("results", [])) != len(STRATEGIES)
    ]
    complete = (
        not missing
        and not extra
        and not invalid
        and len(scenarios) == len(planned)
    )
    strategy_count = sum(len(row.get("results", [])) for row in scenarios)
    status = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "research_section": section,
        "repository_commit": repository_commit,
        "expected_scenarios": len(planned),
        "actual_scenarios": len(scenarios),
        "expected_strategy_results": len(planned) * len(STRATEGIES),
        "actual_strategy_results": strategy_count,
        "missing_scenario_ids": missing,
        "unexpected_scenario_ids": extra,
        "invalid_scenario_count": len(invalid),
        "matrix_complete": complete,
        "ranking_allowed": complete,
        "disclosure": (
            "Historical execution is not a guarantee of future performance."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "coverage-report.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not complete:
        raise ValueError("incomplete short-delay campaign matrix; aggregation blocked")
    rows: list[dict[str, Any]] = []
    trajectory_rows: list[dict[str, Any]] = []
    ordered = sorted(
        scenarios,
        key=lambda row: row["scenario"]["scenario_id"],
    )
    for scenario in ordered:
        metadata = scenario["scenario"]
        for result in scenario["results"]:
            diagnostics = result["delay_diagnostics"]
            rows.append(
                {
                    "scenario_id": metadata["scenario_id"],
                    "research_section": section,
                    "window_set_id": metadata["window_set_id"],
                    "timerange": metadata["timerange"],
                    "cost_profile_id": metadata["cost_profile_id"],
                    "strategy_id": result["strategy"]["strategy_id"],
                    "final_value": result["final_value_exact"],
                    "btc_quantity": result["quantity_exact"],
                    "cash_balance": result["cash_balance_exact"],
                    "delayed_contributions": diagnostics[
                        "delayed_contribution_count"
                    ],
                    "average_delay_days": diagnostics["average_delay_days"],
                    "maximum_delay_days": diagnostics["maximum_delay_days"],
                    "immediate_investment_rate": diagnostics[
                        "immediate_investment_rate"
                    ],
                }
            )
            if section == "historical-complement":
                for purchase in result["purchase_ledger"]:
                    trajectory_rows.append(
                        {
                            "scenario_id": metadata["scenario_id"],
                            "cost_profile_id": metadata["cost_profile_id"],
                            "strategy_id": result["strategy"]["strategy_id"],
                            "executed_at": purchase["executed_at"],
                            "marked_to_market_portfolio_value": purchase[
                                "marked_to_market_portfolio_value"
                            ],
                            "cumulative_quantity": purchase[
                                "cumulative_quantity"
                            ],
                            "residual_cash": purchase["residual_cash"],
                        }
                    )
    with (output_dir / "comparison.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if trajectory_rows:
        with (output_dir / "long-horizon-trajectory.csv").open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(trajectory_rows[0]),
            )
            writer.writeheader()
            writer.writerows(trajectory_rows)
    summary = [
        "# Short-delay DCA research campaign",
        "",
        f"Section: `{section}`",
        f"Complete scenarios: **{len(scenarios)}/{len(planned)}**",
        f"Strategy results: **{len(rows)}**",
        "",
        (
            "The matrix is complete. Historical execution is not a guarantee "
            "of future performance."
        ),
    ]
    (output_dir / "job-summary.md").write_text(
        "\n".join(summary) + "\n",
        encoding="utf-8",
    )
    return status


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--output", type=Path, required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--section", choices=SECTIONS, required=True)
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run-scenario")
    run.add_argument("--scenario", type=Path, required=True)
    run.add_argument(
        "--data-dir",
        type=Path,
        default=Path("user_data/data/kraken"),
    )
    run.add_argument("--repository-commit", required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--section", choices=SECTIONS, required=True)
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    campaign = load_campaign(args.campaign)
    provenance = campaign_provenance(campaign)
    if args.command == "validate":
        payload = {
            "campaign": campaign,
            "provenance": provenance,
            "scenario_counts": {
                section: len(plan_campaign(campaign, section))
                for section in SECTIONS
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    elif args.command == "plan":
        rows = plan_campaign(campaign, args.section)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif args.command == "run-scenario":
        run_scenario(
            campaign=campaign,
            scenario=_load_json(args.scenario),
            data_dir=args.data_dir,
            repository_commit=args.repository_commit,
            output_dir=args.output_dir,
        )
    else:
        files = sorted(args.results_dir.rglob("short-delay-scenario.json"))
        aggregate_campaign(
            campaign=campaign,
            section=args.section,
            result_files=files,
            repository_commit=args.repository_commit,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()
