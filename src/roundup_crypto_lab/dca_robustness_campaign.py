"""Deterministic rolling-window robustness aggregation for controlled DCA campaigns."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "dca-robustness-results/v1"
SURVIVOR_VERSION = "dca-robustness-survivors/v1"


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _canonical(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _month_add(value: datetime, months: int) -> datetime:
    total = value.year * 12 + value.month - 1 + months
    year, month = divmod(total, 12)
    return value.replace(year=year, month=month + 1, day=1)


def _parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def plan_campaign(campaign: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for window_set in campaign["window_sets"]:
        start = _parse_day(window_set["start"])
        limit = _parse_day(window_set["end"])
        months = int(window_set["months"])
        step = int(window_set["step_months"])
        cursor = start
        while _month_add(cursor, months) <= limit:
            end = _month_add(cursor, months)
            timerange = f"{cursor:%Y%m%d}-{end:%Y%m%d}"
            for pair in campaign["pairs"]:
                identity = (pair, window_set["window_set_id"], timerange)
                if identity in identities:
                    raise ValueError(f"duplicate campaign scenario: {identity}")
                identities.add(identity)
                rows.append(
                    {
                        "pair": pair,
                        "window_set_id": window_set["window_set_id"],
                        "phase": window_set["phase"],
                        "timerange": timerange,
                        "months": months,
                        "step_months": step,
                        "overlapping": bool(window_set["overlapping"]),
                    }
                )
            cursor = _month_add(cursor, step)
    return rows


def materialize_registry(
    registry: Mapping[str, Any], strategy_id: str, overrides: Mapping[str, Any]
) -> dict[str, Any]:
    result = deepcopy(registry)
    matches = [row for row in result["strategies"] if row["strategy_id"] == strategy_id]
    if len(matches) != 1:
        raise ValueError(f"strategy identity is not unique: {strategy_id}")
    parameters = matches[0]["parameters"]
    unknown = sorted(set(overrides) - set(parameters))
    if unknown:
        raise ValueError(f"unknown parameter override for {strategy_id}: {unknown}")
    parameters.update({key: str(value) for key, value in overrides.items()})
    result["registry_id"] = f"{registry['registry_id']}::{strategy_id}::neighborhood"
    return result


def _median(values: Iterable[Decimal]) -> Decimal | None:
    materialized = list(values)
    return Decimal(str(statistics.median(materialized))) if materialized else None


def _rank_dispersion(values: list[int]) -> Decimal | None:
    return Decimal(str(statistics.pstdev(values))) if len(values) > 1 else Decimal("0")


def _classification(summary: Mapping[str, Any], policy: Mapping[str, Any]) -> str:
    thresholds = policy["thresholds"]
    evaluated = int(summary["scenarios_evaluated"])
    if evaluated < int(thresholds["minimum_evaluated_windows"]):
        return "unstable"
    no_buy_rate = _decimal(summary["no_buy_or_underdeployed_rate"])
    value_rate = _decimal(summary["final_value_win_rate"])
    quantity_rate = _decimal(summary["quantity_win_rate"])
    deployment = summary["median_capital_deployment_ratio"]
    dispersion = _decimal(summary["rank_dispersion"])
    if no_buy_rate >= _decimal(thresholds["inactive_no_buy_rate"]):
        return "inactive"
    if value_rate <= _decimal(thresholds["rejected_final_value_win_rate_max"]):
        return "rejected"
    if dispersion >= _decimal(thresholds["unstable_rank_dispersion_min"]):
        return "unstable"
    if (
        value_rate >= _decimal(thresholds["robust_improvement_final_value_win_rate"])
        and quantity_rate >= _decimal(thresholds["robust_improvement_quantity_win_rate"])
    ):
        if deployment is not None and _decimal(deployment) <= _decimal(
            thresholds["cash_heavy_median_deployment_ratio_max"]
        ):
            return "promising but cash-heavy"
        return "robust improvement"
    if value_rate >= _decimal(thresholds["promising_final_value_win_rate"]):
        return "regime-dependent"
    return "unstable"


def _scenario_rows(payload: Mapping[str, Any], source: Path) -> list[dict[str, Any]]:
    scenario = payload["scenario"]
    pair = scenario["pair"]
    timerange = scenario["timerange"]
    phase = payload.get("campaign", {}).get("phase", "exploratory")
    window_set_id = payload.get("campaign", {}).get("window_set_id", "unassigned")
    variant_id = payload.get("campaign", {}).get("variant_id", "frozen-default")
    rows: list[dict[str, Any]] = []
    for row in payload["comparison"]:
        item = dict(row)
        item.update(
            {
                "pair": pair,
                "timerange": timerange,
                "phase": phase,
                "window_set_id": window_set_id,
                "variant_id": variant_id,
                "source": str(source),
            }
        )
        rows.append(item)
    return rows


def aggregate_campaign(
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
    result_files: Sequence[Path],
    registry_digest: str,
    repository_commit: str,
) -> dict[str, Any]:
    planned = plan_campaign(campaign)
    planned_ids = {(row["pair"], row["window_set_id"], row["timerange"]) for row in planned}
    rows: list[dict[str, Any]] = []
    scenario_identities: set[tuple[str, str, str, str]] = set()
    incompatible: list[dict[str, Any]] = []
    for path in sorted(result_files):
        payload = load_json(path)
        scenario = payload.get("scenario", {})
        campaign_meta = payload.get("campaign", {})
        identity = (
            str(scenario.get("pair")),
            str(campaign_meta.get("window_set_id")),
            str(scenario.get("timerange")),
            str(campaign_meta.get("variant_id", "frozen-default")),
        )
        if identity in scenario_identities:
            raise ValueError(f"duplicate scenario result: {identity}")
        scenario_identities.add(identity)
        base_identity = identity[:3]
        if base_identity not in planned_ids:
            incompatible.append({"source": str(path), "reason": "scenario identity not planned"})
            continue
        if payload.get("registry", {}).get("registry_digest") != registry_digest:
            incompatible.append({"source": str(path), "reason": "registry digest mismatch"})
            continue
        rows.extend(_scenario_rows(payload, path))
    if incompatible:
        raise ValueError(f"incompatible campaign scenarios: {incompatible}")

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["pair"], row["method"], row["phase"])].append(row)
    summaries: list[dict[str, Any]] = []
    for (pair, method, phase), items in sorted(grouped.items()):
        controls = {
            (row["timerange"], row["window_set_id"], row["variant_id"]): row
            for row in rows
            if row["pair"] == pair and row["method"] == policy["control"] and row["phase"] == phase
        }
        differences: list[Decimal] = []
        quantity_differences: list[Decimal] = []
        ranks: list[int] = []
        value_wins = quantity_wins = profitable = no_buy = 0
        xirr: list[Decimal] = []
        twr: list[Decimal] = []
        drawdowns: list[Decimal] = []
        deployments: list[Decimal] = []
        cash_ages: list[Decimal] = []
        fees: list[Decimal] = []
        actions: list[int] = []
        for row in items:
            key = (row["timerange"], row["window_set_id"], row["variant_id"])
            control = controls.get(key)
            if control is None:
                continue
            final_difference = _decimal(row["final_value"]) - _decimal(control["final_value"])
            quantity_difference = _decimal(row["final_crypto_quantity"]) - _decimal(
                control["final_crypto_quantity"]
            )
            differences.append(final_difference)
            quantity_differences.append(quantity_difference)
            value_wins += final_difference > 0
            quantity_wins += quantity_difference > 0
            profitable += _decimal(row["profit"]) > 0
            actions.append(int(row["action_count"]))
            fees.append(_decimal(row["fees"]))
            deployment = row.get("capital_deployment_ratio")
            if deployment not in (None, ""):
                deployments.append(_decimal(deployment))
            cash_age = row.get("oldest_retained_cash_age_seconds")
            if cash_age not in (None, ""):
                cash_ages.append(_decimal(cash_age))
            if int(row["action_count"]) == 0 or (
                deployment not in (None, "") and _decimal(deployment) < Decimal("0.25")
            ):
                no_buy += 1
            for key_name, destination in (("xirr", xirr), ("twr", twr), ("raw_drawdown", drawdowns)):
                value = row.get(key_name)
                if value not in (None, ""):
                    destination.append(_decimal(value))
            peers = [
                candidate
                for candidate in rows
                if candidate["pair"] == pair
                and candidate["phase"] == phase
                and candidate["timerange"] == row["timerange"]
                and candidate["window_set_id"] == row["window_set_id"]
                and candidate["variant_id"] == row["variant_id"]
            ]
            peers.sort(key=lambda candidate: _decimal(candidate["final_value"]), reverse=True)
            ranks.append(next(index + 1 for index, candidate in enumerate(peers) if candidate["method"] == method))
        evaluated = len(differences)
        summary = {
            "pair": pair,
            "strategy": method,
            "phase": phase,
            "scenarios_evaluated": evaluated,
            "scenarios_missing": max(0, len([row for row in planned if row["pair"] == pair and row["phase"] == phase]) - evaluated),
            "profitable_window_count": profitable,
            "windows_beating_monthly_final_value": value_wins,
            "windows_beating_monthly_quantity": quantity_wins,
            "final_value_win_rate": _canonical(Decimal(value_wins) / evaluated) if evaluated else "0",
            "quantity_win_rate": _canonical(Decimal(quantity_wins) / evaluated) if evaluated else "0",
            "median_final_value_difference": _canonical(_median(differences)) if differences else None,
            "worst_final_value_difference": _canonical(min(differences)) if differences else None,
            "median_quantity_difference": _canonical(_median(quantity_differences)) if quantity_differences else None,
            "worst_quantity_difference": _canonical(min(quantity_differences)) if quantity_differences else None,
            "median_xirr": _canonical(_median(xirr)) if xirr else None,
            "median_twr": _canonical(_median(twr)) if twr else None,
            "median_raw_drawdown": _canonical(_median(drawdowns)) if drawdowns else None,
            "worst_raw_drawdown": _canonical(max(drawdowns)) if drawdowns else None,
            "median_capital_deployment_ratio": _canonical(_median(deployments)) if deployments else None,
            "median_cash_age_seconds": _canonical(_median(cash_ages)) if cash_ages else None,
            "maximum_cash_age_seconds": _canonical(max(cash_ages)) if cash_ages else None,
            "no_buy_or_underdeployed_window_count": no_buy,
            "no_buy_or_underdeployed_rate": _canonical(Decimal(no_buy) / evaluated) if evaluated else "0",
            "median_rank": _canonical(_median(Decimal(value) for value in ranks)) if ranks else None,
            "rank_dispersion": _canonical(_rank_dispersion(ranks)) if ranks else "0",
            "fee_min": _canonical(min(fees)) if fees else None,
            "fee_median": _canonical(_median(fees)) if fees else None,
            "fee_max": _canonical(max(fees)) if fees else None,
            "action_count_min": min(actions) if actions else None,
            "action_count_median": statistics.median(actions) if actions else None,
            "action_count_max": max(actions) if actions else None,
        }
        summary["classification"] = _classification(summary, policy)
        summaries.append(summary)

    survivors = [
        row
        for row in summaries
        if row["phase"] == "exploratory"
        and row["classification"] in {"robust improvement", "promising but cash-heavy", "regime-dependent"}
        and row["strategy"] != policy["control"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"],
        "policy_id": policy["policy_id"],
        "repository_commit": repository_commit,
        "registry_digest": registry_digest,
        "disclosures": campaign["disclosures"],
        "planned_scenarios": planned,
        "aggregate_statistics": summaries,
        "parameter_neighborhoods": campaign.get("parameter_neighborhoods", []),
        "survivor_artifact": {"schema_version": SURVIVOR_VERSION, "survivors": survivors},
    }


def write_outputs(payload: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dca-robustness-campaign.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = payload["aggregate_statistics"]
    with (output_dir / "dca-robustness-campaign.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["pair", "strategy"])
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "dca-robustness-survivors.json").write_text(
        json.dumps(payload["survivor_artifact"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# DCA robustness campaign",
        "",
        "> Overlapping rolling windows are not independent observations.",
        "",
        "Exploratory and confirmation results are reported separately.",
        "",
        "| Pair | Phase | Strategy | Windows | Value win rate | Quantity win rate | Classification |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['pair']} | {row['phase']} | {row['strategy']} | "
            f"{row['scenarios_evaluated']} | {row['final_value_win_rate']} | "
            f"{row['quantity_win_rate']} | {row['classification']} |"
        )
    lines.append("")
    report = "\n".join(lines)
    (output_dir / "dca-robustness-report.md").write_text(report, encoding="utf-8")
    (output_dir / "job-summary.md").write_text(report, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--campaign", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--campaign", type=Path, required=True)
    aggregate.add_argument("--policy", type=Path, required=True)
    aggregate.add_argument("--registry-digest", required=True)
    aggregate.add_argument("--repository-commit", required=True)
    aggregate.add_argument("--results-dir", type=Path, required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    campaign = load_json(args.campaign)
    if args.command == "plan":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan_campaign(campaign), indent=2, sort_keys=True) + "\n")
        return
    policy = load_json(args.policy)
    files = sorted(args.results_dir.rglob("controlled-comparison.json"))
    payload = aggregate_campaign(
        campaign=campaign,
        policy=policy,
        result_files=files,
        registry_digest=args.registry_digest,
        repository_commit=args.repository_commit,
    )
    write_outputs(payload, args.output_dir)


if __name__ == "__main__":
    main()
