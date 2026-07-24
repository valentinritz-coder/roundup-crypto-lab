import copy
import json
from datetime import UTC, datetime

import pytest

from roundup_crypto_lab.investment_plan import InvestmentPlan
from roundup_crypto_lab.scenario_passive import _events_for_mode
from roundup_crypto_lab.unified_comparison import build_unified_comparison

START = "2026-01-25T00:00:00+00:00"
SCHEDULE = [
    {"contributed_at": START, "amount": "40", "kind": "initial"},
    {
        "contributed_at": "2026-02-01T00:00:00+00:00",
        "amount": "40",
        "kind": "monthly",
    },
]


def metric_block(
    final_value: str = "80",
    twr: str = "0",
) -> dict[str, object]:
    return {
        "schema_version": "cash-flow-metrics/v1",
        "cash_flow_timing": "fixture",
        "xirr_terminal_value_basis": "fixture",
        "initial_capital": "40",
        "monthly_budget": "40",
        "total_contributions": "80",
        "total_fees": "0.20",
        "final_value": final_value,
        "final_cash": "10",
        "final_asset_value": str(float(final_value) - 10),
        "terminal_liquidation_value": str(float(final_value) - 0.18),
        "profit_abs": str(float(final_value) - 80),
        "simple_return_on_contributions": str(
            (float(final_value) - 80) / 80
        ),
        "time_weighted_return": twr,
        "money_weighted_return": "0",
        "money_weighted_return_status": "converged",
        "max_drawdown_time_weighted": "0.10",
        "max_drawdown_raw_portfolio": "0.08",
        "average_capital_deployed": "50",
        "capital_utilization_ratio": "0.70",
    }


def active_payload(
    *,
    pair: str = "BTC/EUR",
    mode: str = "recurring_monthly_contributions",
    monthly_budget: str = "40",
    fee: str = "0.0026",
    schedule: list[dict[str, str]] | None = None,
    strategy: str = "StrategyA",
) -> dict[str, object]:
    plan = {
        "initial_capital": "40",
        "monthly_budget": monthly_budget,
        "fee_ratio": fee,
        "contribution_day": 1,
    }
    experiment = {
        "strategy": strategy,
        "selected_pair": pair,
        "timeframe": "4h",
        "timerange": "20260125-20260723",
        "capital_mode": mode,
        "investment_plan": plan,
        "execution_model": "fixture-v1",
    }
    return {
        "native_metadata": {"commit_sha": "abc123"},
        "active_investor_cash_flow_simulation": [
            {
                "schema_version": "active-strategy-result/v1",
                "experiment": experiment,
                "adapter_metrics": {"entry_count": 2},
                "cash_flow_metrics": metric_block("82", "0.02"),
                "contribution_schedule": (
                    schedule or copy.deepcopy(SCHEDULE)
                ),
            }
        ],
    }


def passive_payload(
    *,
    pair: str = "BTC/EUR",
    mode: str = "recurring_monthly_contributions",
    monthly_budget: str = "40",
    fee: str = "0.0026",
    schedule: list[dict[str, str]] | None = None,
    method: str = "MonthlyDCA",
) -> dict[str, object]:
    actual_schedule = schedule or copy.deepcopy(SCHEDULE)
    return {
        "schema_version": "passive-benchmarks/v2",
        "metadata": {
            "timerange": "20260125-20260723",
            "timeframe": "4h",
            "fee": fee,
            "initial_capital": "40",
            "monthly_budget": monthly_budget,
            "contribution_day": 1,
            "capital_mode": mode,
            "repository_commit": "abc123",
            "contribution_schedule": actual_schedule,
        },
        "benchmarks": [
            {
                "benchmark": method,
                "pair": pair,
                "number_of_buys": 2,
                "deployment_method": "monthly_dca",
                "contribution_schedule": actual_schedule,
                "cash_flow_metrics": metric_block("81", "0.01"),
            }
        ],
    }


def test_compatible_active_and_passive_rows_share_one_ranked_group() -> None:
    result = build_unified_comparison(
        [active_payload()],
        [passive_payload()],
        strict_single_scenario=True,
    )
    assert result["schema_version"] == "unified-scenario-comparison/v1"
    assert len(result["scenario_groups"]) == 1
    rows = result["scenario_groups"][0]["rows"]
    assert {row["category"] for row in rows} == {"active", "passive"}
    active = next(row for row in rows if row["category"] == "active")
    assert active["strategy_skill_rank"] == 1
    assert active["investor_outcome_rank"] == 1


@pytest.mark.parametrize(
    "active,passive",
    [
        (active_payload(monthly_budget="41"), passive_payload()),
        (active_payload(fee="0.003"), passive_payload()),
        (active_payload(pair="ETH/EUR"), passive_payload()),
        (active_payload(mode="one_shot_capital"), passive_payload()),
        (
            active_payload(
                schedule=[
                    {
                        "contributed_at": START,
                        "amount": "40",
                        "kind": "initial",
                    },
                    {
                        "contributed_at": "2026-02-02T00:00:00+00:00",
                        "amount": "40",
                        "kind": "monthly",
                    },
                ]
            ),
            passive_payload(),
        ),
    ],
)
def test_incompatible_inputs_cannot_be_ranked_as_one_scenario(
    active: dict[str, object],
    passive: dict[str, object],
) -> None:
    with pytest.raises(
        ValueError,
        match="do not share one comparable scenario",
    ):
        build_unified_comparison(
            [active],
            [passive],
            strict_single_scenario=True,
        )


def test_btc_and_eth_remain_separate_without_strict_mode() -> None:
    result = build_unified_comparison(
        [
            active_payload(pair="BTC/EUR"),
            active_payload(pair="ETH/EUR"),
        ],
        [
            passive_payload(pair="BTC/EUR"),
            passive_payload(pair="ETH/EUR"),
        ],
    )
    assert len(result["scenario_groups"]) == 2
    assert {
        group["scenario"]["pair"]
        for group in result["scenario_groups"]
    } == {"BTC/EUR", "ETH/EUR"}
    pair_scope = result["interpretations"]["pair_scope"]
    assert "not a diversified portfolio" in pair_scope


def test_duplicate_method_identifier_fails() -> None:
    duplicate = passive_payload()
    duplicate["benchmarks"].append(
        copy.deepcopy(duplicate["benchmarks"][0])
    )
    with pytest.raises(
        ValueError,
        match="duplicate scenario and method identifier",
    ):
        build_unified_comparison(
            [active_payload()],
            [duplicate],
            strict_single_scenario=True,
        )


def test_ordering_is_deterministic() -> None:
    first = active_payload(strategy="ZStrategy")
    second = active_payload(strategy="AStrategy")
    first["active_investor_cash_flow_simulation"].extend(
        second["active_investor_cash_flow_simulation"]
    )
    result = build_unified_comparison([first], [passive_payload()])
    methods = [
        row["method"] for row in result["scenario_groups"][0]["rows"]
    ]
    assert methods == ["AStrategy", "ZStrategy", "MonthlyDCA"]


def test_nonfinite_boolean_and_invalid_drawdown_are_rejected() -> None:
    nan_payload = active_payload()
    nan_metrics = nan_payload["active_investor_cash_flow_simulation"][0][
        "cash_flow_metrics"
    ]
    nan_metrics["final_value"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        build_unified_comparison([nan_payload], [passive_payload()])

    boolean_payload = active_payload()
    boolean_metrics = boolean_payload[
        "active_investor_cash_flow_simulation"
    ][0]["cash_flow_metrics"]
    boolean_metrics["total_fees"] = True
    with pytest.raises(ValueError, match="boolean"):
        build_unified_comparison([boolean_payload], [passive_payload()])

    drawdown_payload = active_payload()
    drawdown_metrics = drawdown_payload[
        "active_investor_cash_flow_simulation"
    ][0]["cash_flow_metrics"]
    drawdown_metrics["max_drawdown_time_weighted"] = "1.1"
    with pytest.raises(ValueError, match="between zero and one"):
        build_unified_comparison(
            [drawdown_payload],
            [passive_payload()],
        )


def test_json_output_contains_no_nan_or_infinity() -> None:
    result = build_unified_comparison(
        [active_payload()],
        [passive_payload()],
    )
    json.dumps(result, allow_nan=False)


def test_passive_event_modes_use_initial_only_for_one_shot() -> None:
    plan = InvestmentPlan("40", "40", "0.0026", 1)
    start = datetime(2026, 1, 25, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)
    one_shot = _events_for_mode(
        plan,
        start,
        end,
        "one_shot_capital",
    )
    recurring = _events_for_mode(
        plan,
        start,
        end,
        "recurring_monthly_contributions",
    )
    assert len(one_shot) == 1
    assert len(recurring) == 3
    assert sum(event.amount for event in one_shot) == 40
    assert sum(event.amount for event in recurring) == 120
