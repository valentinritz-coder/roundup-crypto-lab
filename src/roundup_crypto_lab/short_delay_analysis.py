"""Aggregate short-delay campaign artifacts and apply the frozen final decision rule."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from roundup_crypto_lab.short_delay_campaign import PROFILES, STRATEGIES, load_campaign, plan_campaign

POLICY_SCHEMA_VERSION = "short-delay-dca-decision-policy/v1"
ANALYSIS_SCHEMA_VERSION = "short-delay-dca-analysis/v1"
CONCLUSION_SCHEMA_VERSION = "short-delay-dca-final-conclusion/v1"
CONTROL = "monthly_dca_control"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _decimal(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("analysis values must be finite")
    return result


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return Decimal("0") if denominator == 0 else numerator / denominator


def load_policy(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    expected = {
        "schema_version",
        "policy_id",
        "control_strategy_id",
        "candidate_strategy_ids",
        "primary_cost_profile_id",
        "minimum_window_win_rate",
        "minimum_positive_window_set_count",
        "minimum_median_terminal_value_improvement_ratio",
        "maximum_worst_window_deterioration_ratio",
        "minimum_btc_improvement_rate",
        "maximum_forced_release_rate",
        "require_positive_long_horizon_terminal_value",
        "require_positive_long_horizon_btc_quantity",
        "tie_break_order",
        "fallback_decision",
        "fallback_statement",
        "disclosures",
    }
    if set(payload) != expected:
        raise ValueError("short-delay decision policy keys drifted")
    if payload["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ValueError(f"policy schema must be {POLICY_SCHEMA_VERSION}")
    candidates = tuple(payload["candidate_strategy_ids"])
    if payload["control_strategy_id"] != CONTROL or candidates != STRATEGIES[1:]:
        raise ValueError("policy strategy set differs from frozen campaign")
    if tuple(payload["tie_break_order"]) != candidates:
        raise ValueError("tie-break order must contain each challenger exactly once")
    if payload["primary_cost_profile_id"] != "proportional-plus-spread-v1":
        raise ValueError("primary cost profile must remain proportional-plus-spread-v1")
    for field in (
        "minimum_window_win_rate",
        "minimum_median_terminal_value_improvement_ratio",
        "maximum_worst_window_deterioration_ratio",
        "minimum_btc_improvement_rate",
        "maximum_forced_release_rate",
    ):
        value = _decimal(payload[field])
        if value < 0 or value > 1:
            raise ValueError(f"{field} must be between zero and one")
    if payload["minimum_positive_window_set_count"] != 3:
        raise ValueError("all three committed multi-window sets must be positive")
    if payload["fallback_decision"] != "retain_monthly_dca":
        raise ValueError("fallback decision must retain MonthlyDCA")
    return payload


def _scenario_index(files: Sequence[Path]) -> dict[str, dict[str, Any]]:
    scenarios: dict[str, dict[str, Any]] = {}
    for path in sorted(files):
        scenario = _load_json(path)
        identifier = str(scenario["scenario"]["scenario_id"])
        if identifier in scenarios:
            raise ValueError(f"duplicate scenario artifact: {identifier}")
        scenarios[identifier] = scenario
    return scenarios


def _validate_coverage(
    campaign: Mapping[str, Any],
    scenarios: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    planned = [
        *plan_campaign(campaign, "multi-window"),
        *plan_campaign(campaign, "historical-complement"),
    ]
    expected = {str(row["scenario_id"]) for row in planned}
    actual = set(scenarios)
    invalid = [
        identifier
        for identifier, scenario in scenarios.items()
        if tuple(result["strategy"]["strategy_id"] for result in scenario["results"])
        != STRATEGIES
    ]
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    complete = not missing and not extra and not invalid and len(actual) == len(expected)
    return {
        "expected_scenarios": len(expected),
        "actual_scenarios": len(actual),
        "expected_strategy_results": len(expected) * len(STRATEGIES),
        "actual_strategy_results": sum(len(row["results"]) for row in scenarios.values()),
        "missing_scenario_ids": missing,
        "unexpected_scenario_ids": extra,
        "invalid_scenario_ids": sorted(invalid),
        "matrix_complete": complete,
    }


def _results_by_strategy(scenario: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    results = {
        str(result["strategy"]["strategy_id"]): result for result in scenario["results"]
    }
    if set(results) != set(STRATEGIES):
        raise ValueError("scenario does not contain the exact frozen strategy set")
    return results


def _contribution_rows(
    scenario: Mapping[str, Any],
    control: Mapping[str, Any],
    challenger: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metadata = scenario["scenario"]
    control_allocations = {
        str(row["contribution_id"]): row for row in control["funding_allocations"]
    }
    challenger_allocations = {
        str(row["contribution_id"]): row for row in challenger["funding_allocations"]
    }
    if set(control_allocations) != set(challenger_allocations):
        raise ValueError("challenger contribution identities differ from MonthlyDCA")
    rows: list[dict[str, Any]] = []
    for contribution_id in sorted(control_allocations):
        baseline = control_allocations[contribution_id]
        candidate = challenger_allocations[contribution_id]
        baseline_price = _decimal(baseline["execution_price"])
        candidate_price = _decimal(candidate["execution_price"])
        baseline_quantity = _decimal(baseline["btc_quantity"])
        candidate_quantity = _decimal(candidate["btc_quantity"])
        rows.append(
            {
                "scenario_id": metadata["scenario_id"],
                "research_section": metadata["research_section"],
                "window_set_id": metadata["window_set_id"],
                "timerange": metadata["timerange"],
                "cost_profile_id": metadata["cost_profile_id"],
                "strategy_id": challenger["strategy"]["strategy_id"],
                "contribution_id": contribution_id,
                "contributed_at": candidate["contributed_at"],
                "executed_at": candidate["executed_at"],
                "release_type": candidate["release_type"],
                "waiting_seconds": candidate["waiting_seconds"],
                "monthly_dca_price": baseline_price,
                "challenger_price": candidate_price,
                "price_difference": candidate_price - baseline_price,
                "price_improvement_ratio": _ratio(
                    baseline_price - candidate_price,
                    baseline_price,
                ),
                "monthly_dca_btc_quantity": baseline_quantity,
                "challenger_btc_quantity": candidate_quantity,
                "btc_quantity_difference": candidate_quantity - baseline_quantity,
                "explicit_fees_difference": _decimal(candidate["explicit_fees"])
                - _decimal(baseline["explicit_fees"]),
                "spread_cost_difference": _decimal(candidate["estimated_spread_cost"])
                - _decimal(baseline["estimated_spread_cost"]),
            }
        )
    return rows


def _window_row(
    scenario: Mapping[str, Any],
    control: Mapping[str, Any],
    challenger: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = scenario["scenario"]
    control_value = _decimal(control["final_value_exact"])
    challenger_value = _decimal(challenger["final_value_exact"])
    control_quantity = _decimal(control["quantity_exact"])
    challenger_quantity = _decimal(challenger["quantity_exact"])
    control_costs = control["execution_costs"]
    challenger_costs = challenger["execution_costs"]
    diagnostics = challenger["delay_diagnostics"]
    return {
        "scenario_id": metadata["scenario_id"],
        "research_section": metadata["research_section"],
        "window_set_id": metadata["window_set_id"],
        "timerange": metadata["timerange"],
        "cost_profile_id": metadata["cost_profile_id"],
        "strategy_id": challenger["strategy"]["strategy_id"],
        "terminal_value": challenger_value,
        "terminal_value_difference": challenger_value - control_value,
        "terminal_value_difference_ratio": _ratio(
            challenger_value - control_value,
            control_value,
        ),
        "btc_quantity": challenger_quantity,
        "btc_quantity_difference": challenger_quantity - control_quantity,
        "cash_balance": _decimal(challenger["cash_balance_exact"]),
        "execution_cost": _decimal(challenger_costs["total_execution_cost"]),
        "execution_cost_difference": _decimal(challenger_costs["total_execution_cost"])
        - _decimal(control_costs["total_execution_cost"]),
        "delayed_contribution_rate": Decimal("1")
        - _decimal(diagnostics["immediate_investment_rate"]),
        "average_delay_days": _decimal(diagnostics["average_delay_days"]),
        "maximum_delay_days": _decimal(diagnostics["maximum_delay_days"]),
        "forced_release_rate": _ratio(
            Decimal(str(diagnostics.get("forced_contribution_count", 0))),
            Decimal(str(diagnostics["contribution_count"])),
        ),
        "maximum_drawdown": _decimal(challenger["maximum_drawdown_exact"]),
        "monthly_dca_maximum_drawdown": _decimal(control["maximum_drawdown_exact"]),
        "average_cash_balance": _decimal(challenger["average_cash_balance_exact"]),
        "capital_deployment_ratio": _decimal(challenger["capital_deployment_ratio_exact"]),
    }


def _median(values: Sequence[Decimal]) -> Decimal:
    return Decimal(str(median(values)))


def _rule_summaries(
    window_rows: Sequence[Mapping[str, Any]],
    contribution_rows: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    primary = str(policy["primary_cost_profile_id"])
    summaries: list[dict[str, Any]] = []
    for strategy_id in STRATEGIES[1:]:
        windows = [
            row
            for row in window_rows
            if row["strategy_id"] == strategy_id and row["cost_profile_id"] == primary
        ]
        multi = [row for row in windows if row["research_section"] == "multi-window"]
        complement = [
            row for row in windows if row["research_section"] == "historical-complement"
        ]
        contributions = [
            row
            for row in contribution_rows
            if row["strategy_id"] == strategy_id and row["cost_profile_id"] == primary
        ]
        if not multi or len(complement) != 1 or not contributions:
            raise ValueError(f"incomplete primary-profile evidence for {strategy_id}")
        by_set: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in multi:
            by_set[str(row["window_set_id"])].append(row)
        set_rows = []
        for window_set_id, group in sorted(by_set.items()):
            differences = [_decimal(row["terminal_value_difference_ratio"]) for row in group]
            set_rows.append(
                {
                    "window_set_id": window_set_id,
                    "window_count": len(group),
                    "median_terminal_value_difference_ratio": _median(differences),
                    "win_rate": _ratio(
                        Decimal(sum(value > 0 for value in differences)),
                        Decimal(len(differences)),
                    ),
                }
            )
        value_differences = [
            _decimal(row["terminal_value_difference_ratio"]) for row in multi
        ]
        btc_differences = [_decimal(row["btc_quantity_difference"]) for row in multi]
        forced = [_decimal(row["forced_release_rate"]) for row in multi]
        contribution_btc = [_decimal(row["btc_quantity_difference"]) for row in contributions]
        summary = {
            "strategy_id": strategy_id,
            "primary_cost_profile_id": primary,
            "window_count": len(multi),
            "median_terminal_value_difference_ratio": _median(value_differences),
            "worst_terminal_value_difference_ratio": min(value_differences),
            "window_win_rate": _ratio(
                Decimal(sum(value > 0 for value in value_differences)),
                Decimal(len(value_differences)),
            ),
            "positive_window_set_count": sum(
                row["median_terminal_value_difference_ratio"] > 0
                and row["win_rate"] >= _decimal(policy["minimum_window_win_rate"])
                for row in set_rows
            ),
            "window_set_evidence": set_rows,
            "median_btc_quantity_difference": _median(btc_differences),
            "btc_positive_window_rate": _ratio(
                Decimal(sum(value > 0 for value in btc_differences)),
                Decimal(len(btc_differences)),
            ),
            "contribution_btc_improvement_rate": _ratio(
                Decimal(sum(value > 0 for value in contribution_btc)),
                Decimal(len(contribution_btc)),
            ),
            "median_contribution_btc_difference": _median(contribution_btc),
            "average_forced_release_rate": sum(forced, Decimal("0"))
            / Decimal(len(forced)),
            "long_horizon_terminal_value_difference_ratio": _decimal(
                complement[0]["terminal_value_difference_ratio"]
            ),
            "long_horizon_btc_quantity_difference": _decimal(
                complement[0]["btc_quantity_difference"]
            ),
        }
        summaries.append(summary)
    return summaries


def _qualifies(summary: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[bool, list[str]]:
    checks = {
        "meaningful_net_improvement": _decimal(
            summary["median_terminal_value_difference_ratio"]
        )
        >= _decimal(policy["minimum_median_terminal_value_improvement_ratio"]),
        "broad_window_win_rate": _decimal(summary["window_win_rate"])
        >= _decimal(policy["minimum_window_win_rate"]),
        "all_window_sets_positive": int(summary["positive_window_set_count"])
        >= int(policy["minimum_positive_window_set_count"]),
        "worst_case_guardrail": _decimal(summary["worst_terminal_value_difference_ratio"])
        >= -_decimal(policy["maximum_worst_window_deterioration_ratio"]),
        "btc_improvement": _decimal(summary["contribution_btc_improvement_rate"])
        >= _decimal(policy["minimum_btc_improvement_rate"])
        and _decimal(summary["median_btc_quantity_difference"]) > 0,
        "limited_forced_release": _decimal(summary["average_forced_release_rate"])
        <= _decimal(policy["maximum_forced_release_rate"]),
        "positive_long_horizon_value": _decimal(
            summary["long_horizon_terminal_value_difference_ratio"]
        )
        > 0,
        "positive_long_horizon_btc": _decimal(
            summary["long_horizon_btc_quantity_difference"]
        )
        > 0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return not failed, failed


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze_campaign(
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
    result_files: Sequence[Path],
    repository_commit: str,
    output_dir: Path,
) -> dict[str, Any]:
    scenarios = _scenario_index(result_files)
    coverage = _validate_coverage(campaign, scenarios)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "coverage-report.json").write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not coverage["matrix_complete"]:
        raise ValueError("complete 52-scenario matrix required for final analysis")

    window_rows: list[dict[str, Any]] = []
    contribution_rows: list[dict[str, Any]] = []
    for scenario in scenarios.values():
        results = _results_by_strategy(scenario)
        control = results[CONTROL]
        for strategy_id in STRATEGIES[1:]:
            challenger = results[strategy_id]
            window_rows.append(_window_row(scenario, control, challenger))
            contribution_rows.extend(_contribution_rows(scenario, control, challenger))

    summaries = _rule_summaries(window_rows, contribution_rows, policy)
    ranked = sorted(
        summaries,
        key=lambda row: (
            -_decimal(row["median_terminal_value_difference_ratio"]),
            -_decimal(row["worst_terminal_value_difference_ratio"]),
            tuple(policy["tie_break_order"]).index(row["strategy_id"]),
        ),
    )
    qualifications = []
    for rank, summary in enumerate(ranked, start=1):
        passed, failed = _qualifies(summary, policy)
        summary["rank"] = rank
        summary["adoption_qualified"] = passed
        summary["failed_adoption_checks"] = failed
        qualifications.append(summary)
    eligible = [row for row in qualifications if row["adoption_qualified"]]
    if eligible:
        selected = eligible[0]
        decision = "adopt_short_delay_rule"
        selected_strategy_id: str | None = str(selected["strategy_id"])
        statement = (
            "Adopt the frozen short-delay rule "
            f"{selected_strategy_id}; retain all protocol parameters unchanged."
        )
    else:
        decision = str(policy["fallback_decision"])
        selected_strategy_id = None
        statement = str(policy["fallback_statement"])

    conclusion = {
        "schema_version": CONCLUSION_SCHEMA_VERSION,
        "repository_commit": repository_commit,
        "policy_id": policy["policy_id"],
        "decision": decision,
        "selected_strategy_id": selected_strategy_id,
        "statement": statement,
        "control_strategy_id": CONTROL,
        "primary_cost_profile_id": policy["primary_cost_profile_id"],
        "candidate_assessments": qualifications,
        "disclosures": policy["disclosures"],
    }
    analysis = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "repository_commit": repository_commit,
        "coverage": coverage,
        "rule_summaries": qualifications,
        "final_conclusion": conclusion,
    }
    _write_csv(output_dir / "window-level.csv", window_rows)
    _write_csv(output_dir / "contribution-level.csv", contribution_rows)
    _write_csv(output_dir / "rule-summary.csv", qualifications)
    (output_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "final-conclusion.json").write_text(
        json.dumps(conclusion, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Short-delay DCA final analysis",
        "",
        f"Decision: **{statement}**",
        "",
        "| Rank | Rule | Median value diff | Worst window | Win rate | Qualified |",
        "| ---: | --- | ---: | ---: | ---: | :---: |",
    ]
    for row in qualifications:
        lines.append(
            f"| {row['rank']} | {row['strategy_id']} | "
            f"{row['median_terminal_value_difference_ratio']} | "
            f"{row['worst_terminal_value_difference_ratio']} | "
            f"{row['window_win_rate']} | "
            f"{'yes' if row['adoption_qualified'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "Lower drawdown or exposure alone never qualifies a challenger for adoption.",
            "Historical execution is not a guarantee of future performance.",
        ]
    )
    (output_dir / "job-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    interpretation = [
        "# Short-delay DCA research interpretation",
        "",
        "MonthlyDCA remains the confirmed benchmark and each challenger is compared on matching "
        "contributions, market windows and cost assumptions.",
        "",
        f"## Final decision\n\n{statement}",
        "",
        "The decision policy requires positive after-cost value, positive BTC evidence, broad "
        "window support, a bounded worst case, limited forced deployment and consistent long-horizon "
        "evidence. Reduced exposure or drawdown cannot substitute for return improvement.",
        "",
        "The continuous path is retained only as a historical complement. Overlapping windows are "
        "dependent observations, and the former passive-frequency confirmation period is not reused "
        "as a new independent holdout.",
    ]
    (output_dir / "interpretation.md").write_text(
        "\n".join(interpretation) + "\n",
        encoding="utf-8",
    )
    return analysis


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    analyze_campaign(
        campaign=load_campaign(args.campaign),
        policy=load_policy(args.policy),
        result_files=sorted(args.results_dir.rglob("short-delay-scenario.json")),
        repository_commit=args.repository_commit,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
