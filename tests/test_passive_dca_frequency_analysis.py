from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.deployment_engine import parse_timerange
from roundup_crypto_lab.execution_costs import resolve_cost_profile
from roundup_crypto_lab.investment_plan import InvestmentPlan, contribution_schedule
from roundup_crypto_lab.passive_dca_frequency_analysis import (
    aggregate_frequency_results,
    write_frequency_analysis_outputs,
)
from roundup_crypto_lab.passive_dca_frequency_campaign import (
    frequency_metadata,
    load_json,
    plan_campaign,
)

CAMPAIGN_PATH = Path("config/passive-dca-frequency-campaign.json")
REGISTRY_PATH = Path("config/passive-dca-frequency-strategies.json")
POLICY_PATH = Path("config/passive-dca-frequency-policy.json")
PROFILE_DIR = Path("config/execution-cost-profiles")

GROSS_VALUES = {
    "weekly-dca": Decimal("101"),
    "monthly-dca": Decimal("100"),
    "every-two-months-phase-0": Decimal("102"),
    "every-two-months-phase-1": Decimal("98"),
    "quarterly-phase-0": Decimal("103"),
    "quarterly-phase-1": Decimal("97"),
    "quarterly-phase-2": Decimal("95"),
}
PROFILE_DRAG = {
    "frictionless-control-v1": Decimal("0"),
    "proportional-fee-v1": Decimal("1"),
    "proportional-plus-spread-v1": Decimal("2"),
}


def _canonical(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _write_confirmation_results(tmp_path: Path) -> list[Path]:
    campaign = load_json(CAMPAIGN_PATH)
    registry = load_registry(REGISTRY_PATH)
    scenarios = [
        row for row in plan_campaign(campaign) if row["phase"] == "confirmation"
    ]
    paths = []
    for scenario in scenarios:
        profile = resolve_cost_profile(
            scenario["cost_profile_id"],
            search_dir=PROFILE_DIR,
        )
        start, end = parse_timerange(scenario["timerange"])
        plan = InvestmentPlan(
            scenario["initial_capital"],
            scenario["monthly_budget"],
            profile.trading_fee_ratio,
            scenario["contribution_day"],
        )
        total_contributions = sum(
            (event.amount for event in contribution_schedule(plan, start, end)),
            Decimal("0"),
        )
        results = []
        for definition in registry.strategies:
            frequency = frequency_metadata(definition)
            gross = GROSS_VALUES[definition.strategy_id]
            drag = PROFILE_DRAG[profile.cost_profile_id]
            net = gross - drag
            explicit = Decimal("0") if drag == 0 else Decimal("1")
            spread = max(Decimal("0"), drag - explicit)
            results.append(
                {
                    "benchmark": definition.strategy_id,
                    "strategy": {
                        "strategy_id": definition.strategy_id,
                        "strategy_version": definition.strategy_version,
                        "implementation": definition.implementation,
                        "parameters": dict(definition.parameters),
                    },
                    "frequency": frequency,
                    "final_value_exact": _canonical(net),
                    "quantity_exact": _canonical(net / Decimal("100")),
                    "cash_balance_exact": "0",
                    "max_drawdown_time_weighted": "0.10",
                    "equity_curve": [
                        {
                            "cash_balance": "40",
                            "cumulative_contributions": "40",
                        },
                        {
                            "cash_balance": "10",
                            "cumulative_contributions": _canonical(total_contributions),
                        },
                    ],
                    "purchase_ledger": [],
                    "execution_costs": {
                        "cost_profile": profile.artifact(),
                        "order_count": 10,
                        "gross_order_total": "400",
                        "average_order_size": "40",
                        "trading_fees_paid": _canonical(explicit),
                        "fixed_order_fees_paid": "0",
                        "explicit_fees_paid": _canonical(explicit),
                        "estimated_spread_cost": _canonical(spread),
                        "total_execution_cost": _canonical(explicit + spread),
                    },
                }
            )
        payload = {
            "schema_version": "passive-dca-frequency-scenario/v1",
            "campaign": {
                "campaign_id": scenario["campaign_id"],
                "phase": scenario["phase"],
                "window_set_id": scenario["window_set_id"],
                "scenario_id": scenario["scenario_id"],
            },
            "scenario": {
                "pair": scenario["pair"],
                "timeframe": scenario["timeframe"],
                "timerange": scenario["timerange"],
                "initial_capital": scenario["initial_capital"],
                "monthly_budget": scenario["monthly_budget"],
                "contribution_day": scenario["contribution_day"],
                "weekly_day": scenario["weekly_day"],
                "total_contributions": _canonical(total_contributions),
                "repository_commit": "a" * 40,
            },
            "registry": {
                "registry_id": registry.registry_id,
                "registry_digest": registry.digest,
            },
            "execution_cost_profile": profile.artifact(),
            "results": results,
            "comparison": [],
        }
        path = (
            tmp_path
            / profile.cost_profile_id
            / scenario["timerange"]
            / "frequency-scenario.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def test_analysis_aggregates_all_nuisance_phases_before_ranking(
    tmp_path: Path,
) -> None:
    campaign = load_json(CAMPAIGN_PATH)
    registry = load_registry(REGISTRY_PATH)
    policy = load_json(POLICY_PATH)
    payload = aggregate_frequency_results(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase="confirmation",
        result_files=_write_confirmation_results(tmp_path),
        repository_commit="a" * 40,
    )

    assert len(payload["phase_level_results"]) == 21
    assert len(payload["window_frequency_aggregates"]) == 12
    quarterly = next(
        row
        for row in payload["window_frequency_aggregates"]
        if row["cost_profile_id"] == "frictionless-control-v1"
        and row["frequency"] == "quarterly"
    )
    assert quarterly["phase_replication_count"] == 3
    assert quarterly["phase_offsets"] == [0, 1, 2]
    assert quarterly["net_terminal_value"] == Decimal("97")
    assert quarterly["worst_phase_offset_months"] == 2
    assert quarterly["worst_phase_terminal_value"] == Decimal("95")
    assert quarterly["phase_dispersion"] > 0

    ranking = [
        row
        for row in payload["rankings"]
        if row["cost_profile_id"] == "frictionless-control-v1"
    ]
    assert [row["frequency"] for row in ranking] == [
        "weekly",
        "monthly",
        "every-2-months",
        "quarterly",
    ]
    assert all("phase_offset_months" not in row for row in ranking)


def test_analysis_separates_timing_costs_cash_and_net_results(
    tmp_path: Path,
) -> None:
    campaign = load_json(CAMPAIGN_PATH)
    registry = load_registry(REGISTRY_PATH)
    policy = load_json(POLICY_PATH)
    payload = aggregate_frequency_results(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase="confirmation",
        result_files=_write_confirmation_results(tmp_path),
        repository_commit="b" * 40,
    )
    weekly = next(
        row
        for row in payload["window_frequency_aggregates"]
        if row["cost_profile_id"] == "proportional-plus-spread-v1"
        and row["frequency"] == "weekly"
    )
    assert weekly["gross_terminal_value"] == Decimal("101")
    assert weekly["net_terminal_value"] == Decimal("99")
    assert weekly["execution_cost_terminal_impact"] == Decimal("2")
    assert weekly["gross_timing_difference_vs_monthly"] == Decimal("1")
    assert weekly["differential_cost_impact_vs_monthly"] == Decimal("0")
    assert weekly["net_difference_vs_monthly"] == Decimal("1")
    assert weekly["average_pending_cash_age_seconds"] > 0
    assert weekly["maximum_pending_cash_age_seconds"] > 0
    assert weekly["average_cash_balance"] > 0
    assert weekly["contribution_neutralized_drawdown"] == Decimal("0.10")


def test_analysis_writes_compact_machine_and_human_outputs(
    tmp_path: Path,
) -> None:
    campaign = load_json(CAMPAIGN_PATH)
    registry = load_registry(REGISTRY_PATH)
    policy = load_json(POLICY_PATH)
    payload = aggregate_frequency_results(
        campaign=campaign,
        registry=registry,
        policy=policy,
        phase="confirmation",
        result_files=_write_confirmation_results(tmp_path / "inputs"),
        repository_commit="c" * 40,
    )
    output = tmp_path / "aggregate"
    write_frequency_analysis_outputs(payload, output)
    assert {
        "passive-frequency-analysis.json",
        "passive-frequency-phase-level.csv",
        "passive-frequency-window-summary.csv",
        "passive-frequency-summary.csv",
        "passive-frequency-cost-decomposition.csv",
        "passive-frequency-rankings.csv",
        "passive-frequency-classification.json",
        "job-summary.md",
    } <= {path.name for path in output.iterdir()}
    summary = (output / "job-summary.md").read_text(encoding="utf-8")
    assert "no best phase is selected" in summary
    assert "Gross timing Δ" in summary
    assert "Cost-impact Δ" in summary
