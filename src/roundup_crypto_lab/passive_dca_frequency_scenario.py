"""Run one committed passive DCA frequency campaign scenario."""

from __future__ import annotations

import argparse
import csv
import json
import platform
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.dca_cost_profile_scenario import run_cost_profile_scenario
from roundup_crypto_lab.execution_costs import DEFAULT_COST_PROFILE_DIR
from roundup_crypto_lab.passive_dca_frequency_campaign import frequency_metadata

SCHEMA_VERSION = "passive-dca-frequency-scenario/v1"
MANIFEST_VERSION = "passive-dca-frequency-scenario-manifest/v1"
STRATEGY_MANIFEST_VERSION = "passive-dca-frequency-strategy-manifest/v1"


def _result_frequency(result: Mapping[str, Any]) -> dict[str, Any]:
    strategy = result.get("strategy")
    if not isinstance(strategy, Mapping):
        raise ValueError("frequency result must contain strategy metadata")

    class DefinitionProjection:
        strategy_id = str(strategy.get("strategy_id", ""))
        implementation = str(strategy.get("implementation", ""))
        parameters = dict(strategy.get("parameters", {}))
        required_indicators: tuple[Any, ...] = ()

    return frequency_metadata(DefinitionProjection())  # type: ignore[arg-type]


def run_frequency_scenario(
    *,
    data_dir: Path,
    pair: str,
    timeframe: str,
    timerange: str,
    registry_path: Path,
    initial_capital: str,
    monthly_budget: str,
    contribution_day: int,
    weekly_day: str,
    cost_profile_reference: str,
    cost_profile_dir: Path,
    repository_commit: str,
    campaign_id: str,
    phase: str,
    window_set_id: str,
    scenario_id: str,
) -> dict[str, Any]:
    """Execute the complete passive matrix for one window and cost profile."""
    if phase not in {"exploratory", "confirmation"}:
        raise ValueError("phase must be exploratory or confirmation")
    for value, name in (
        (campaign_id, "campaign_id"),
        (window_set_id, "window_set_id"),
        (scenario_id, "scenario_id"),
    ):
        if not value.strip():
            raise ValueError(f"{name} must be non-empty")

    payload = run_cost_profile_scenario(
        data_dir=data_dir,
        pair=pair,
        timeframe=timeframe,
        timerange=timerange,
        registry_path=registry_path,
        initial_capital=initial_capital,
        monthly_budget=monthly_budget,
        contribution_day=contribution_day,
        weekly_day=weekly_day,
        repository_commit=repository_commit,
        cost_profile_reference=cost_profile_reference,
        cost_profile_dir=cost_profile_dir,
    )
    profile_id = payload["execution_cost_profile"]["cost_profile_id"]
    if profile_id != cost_profile_reference:
        raise ValueError("resolved cost profile identity differs from planned scenario")

    result_by_strategy = {
        result["strategy"]["strategy_id"]: result for result in payload["results"]
    }
    if len(result_by_strategy) != len(payload["results"]):
        raise ValueError("frequency scenario contains duplicate strategy identities")

    comparison = []
    for row in payload["comparison"]:
        strategy_id = row["strategy_id"]
        result = result_by_strategy.get(strategy_id)
        if result is None:
            raise ValueError(f"comparison row has no strategy result: {strategy_id}")
        frequency = _result_frequency(result)
        result["frequency"] = frequency
        comparison.append({**row, **frequency})

    return {
        "schema_version": SCHEMA_VERSION,
        "campaign": {
            "campaign_id": campaign_id,
            "phase": phase,
            "window_set_id": window_set_id,
            "scenario_id": scenario_id,
        },
        "scenario": payload["scenario"],
        "registry": payload["registry"],
        "execution_cost_profile": payload["execution_cost_profile"],
        "results": payload["results"],
        "comparison": comparison,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["strategy_id"])
        writer.writeheader()
        writer.writerows(rows)


def _strategy_directory(output_dir: Path, result: Mapping[str, Any]) -> Path:
    frequency = result["frequency"]
    return (
        output_dir
        / "frequencies"
        / frequency["frequency"]
        / frequency["phase_path"]
        / result["strategy"]["strategy_id"]
    )


def write_frequency_outputs(payload: Mapping[str, Any], output_dir: Path) -> None:
    """Persist scenario, per-frequency paths, manifests and a concise summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "frequency-scenario.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )
    _write_csv(output_dir / "frequency-comparison.csv", payload["comparison"])
    (output_dir / "resolved-cost-profile.json").write_text(
        json.dumps(
            payload["execution_cost_profile"],
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )

    strategy_artifacts = []
    for result in payload["results"]:
        directory = _strategy_directory(output_dir, result)
        directory.mkdir(parents=True, exist_ok=True)
        result_path = directory / "result.json"
        result_path.write_text(
            json.dumps(result, indent=2, allow_nan=False, default=str) + "\n",
            encoding="utf-8",
        )
        ledger = result.get("purchase_ledger", [])
        if ledger:
            _write_csv(directory / "purchase-ledger.csv", ledger)
        manifest = {
            "schema_version": STRATEGY_MANIFEST_VERSION,
            "campaign": payload["campaign"],
            "scenario": payload["scenario"],
            "registry": payload["registry"],
            "cost_profile_id": payload["execution_cost_profile"]["cost_profile_id"],
            "cost_profile_digest": payload["execution_cost_profile"]["profile_digest"],
            "frequency": result["frequency"],
            "strategy": result["strategy"],
            "result_path": str(result_path.relative_to(output_dir)),
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        strategy_artifacts.append(
            {
                "strategy_id": result["strategy"]["strategy_id"],
                "frequency": result["frequency"],
                "result_path": str(result_path.relative_to(output_dir)),
                "manifest_path": str(
                    (directory / "manifest.json").relative_to(output_dir)
                ),
            }
        )

    manifest = {
        "schema_version": MANIFEST_VERSION,
        "repository_commit": payload["scenario"]["repository_commit"],
        "campaign": payload["campaign"],
        "scenario": payload["scenario"],
        "registry": payload["registry"],
        "execution_cost_profile": payload["execution_cost_profile"],
        "strategy_artifacts": strategy_artifacts,
        "python_version": platform.python_version(),
        "pandas_version": pd.__version__,
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    (output_dir / "reproducibility-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Passive DCA frequency scenario",
        "",
        f"Phase: `{payload['campaign']['phase']}`",
        f"Window: `{payload['scenario']['timerange']}`",
        (
            "Cost profile: "
            f"`{payload['execution_cost_profile']['cost_profile_id']}`"
        ),
        "",
        "| Frequency | Phase offset | Final value | Explicit fees | Spread cost | Orders |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["comparison"]:
        phase = row["phase_offset_months"]
        phase_label = "predefined weekday" if phase is None else str(phase)
        lines.append(
            f"| {row['frequency']} | {phase_label} | {row['final_value']} | "
            f"{row['explicit_fees_paid']} | {row['estimated_spread_cost']} | "
            f"{row['order_count']} |"
        )
    lines.extend(
        [
            "",
            "Calendar phases are recorded as nuisance replications and are not ranked here.",
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
    parser.add_argument("--pair", required=True, choices=("BTC/EUR",))
    parser.add_argument("--timeframe", required=True, choices=("4h",))
    parser.add_argument("--timerange", required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--weekly-day", required=True)
    parser.add_argument("--cost-profile", required=True)
    parser.add_argument(
        "--cost-profile-dir",
        type=Path,
        default=DEFAULT_COST_PROFILE_DIR,
    )
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("exploratory", "confirmation"),
    )
    parser.add_argument("--window-set-id", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    payload = run_frequency_scenario(
        data_dir=args.data_dir,
        pair=args.pair,
        timeframe=args.timeframe,
        timerange=args.timerange,
        registry_path=args.registry,
        initial_capital=args.initial_capital,
        monthly_budget=args.monthly_budget,
        contribution_day=args.contribution_day,
        weekly_day=args.weekly_day,
        cost_profile_reference=args.cost_profile,
        cost_profile_dir=args.cost_profile_dir,
        repository_commit=args.repository_commit,
        campaign_id=args.campaign_id,
        phase=args.phase,
        window_set_id=args.window_set_id,
        scenario_id=args.scenario_id,
    )
    write_frequency_outputs(payload, args.output_dir)


if __name__ == "__main__":
    main()
