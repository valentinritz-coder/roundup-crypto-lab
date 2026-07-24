"""Build and summarize the frozen historical crypto campaign matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from roundup_crypto_lab.historical_scenarios import (
    DEFAULT_REGISTRY,
    HistoricalScenarioRegistry,
    load_registry,
)

MATRIX_SCHEMA_VERSION = "historical-crypto-campaign-matrix/v1"
STATUS_SCHEMA_VERSION = "historical-crypto-campaign-status/v1"
SUMMARY_SCHEMA_VERSION = "historical-crypto-campaign-summary/v1"


def build_matrix(registry: HistoricalScenarioRegistry) -> dict[str, object]:
    """Return the deterministic GitHub Actions matrix consumed by the campaign."""

    include = []
    artifact_names: set[str] = set()
    for scenario in registry.scenarios:
        row = scenario.canonical_mapping()
        artifact_name = f"historical-scenario-{scenario.scenario_id}"
        if artifact_name in artifact_names:
            raise ValueError(f"duplicate campaign artifact name: {artifact_name}")
        artifact_names.add(artifact_name)
        include.append(
            {
                "scenario_id": scenario.scenario_id,
                "registry_id": registry.registry_id,
                "pair": scenario.pair,
                "timeframe": scenario.timeframe,
                "timerange": scenario.timerange,
                "capital_mode": scenario.capital_mode,
                "initial_capital": row["initial_capital"],
                "monthly_budget": row["monthly_budget"],
                "contribution_day": scenario.contribution_day,
                "fee_ratio": row["fee_ratio"],
                "regime_label": scenario.regime_label,
                "artifact_name": artifact_name,
            }
        )
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "registry_id": registry.registry_id,
        "include": include,
    }


def matrix_json(registry: HistoricalScenarioRegistry) -> str:
    return json.dumps(build_matrix(registry), sort_keys=True, allow_nan=False)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def load_status(path: Path) -> dict[str, object]:
    payload = _mapping(json.loads(path.read_text(encoding="utf-8")), "status")
    if payload.get("schema_version") != STATUS_SCHEMA_VERSION:
        raise ValueError(f"unsupported campaign status schema in {path}")
    scenario_id = payload.get("scenario_id")
    registry_id = payload.get("registry_id")
    conclusion = payload.get("conclusion")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError(f"invalid scenario id in {path}")
    if not isinstance(registry_id, str) or not registry_id:
        raise ValueError(f"invalid registry id in {path}")
    if conclusion not in {"success", "failure", "cancelled"}:
        raise ValueError(f"invalid conclusion in {path}")
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "registry_id": registry_id,
        "conclusion": conclusion,
    }


def build_summary(
    registry: HistoricalScenarioRegistry,
    status_paths: list[Path],
) -> dict[str, object]:
    statuses: dict[str, dict[str, object]] = {}
    for path in sorted(status_paths):
        status = load_status(path)
        if status["registry_id"] != registry.registry_id:
            raise ValueError("campaign status registry id does not match frozen registry")
        scenario_id = str(status["scenario_id"])
        if scenario_id in statuses:
            raise ValueError(f"duplicate campaign status: {scenario_id}")
        statuses[scenario_id] = status

    rows = []
    for scenario in registry.scenarios:
        status = statuses.pop(scenario.scenario_id, None)
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "pair": scenario.pair,
                "timeframe": scenario.timeframe,
                "timerange": scenario.timerange,
                "capital_mode": scenario.capital_mode,
                "regime_label": scenario.regime_label,
                "conclusion": "missing" if status is None else status["conclusion"],
            }
        )
    if statuses:
        raise ValueError(
            "campaign statuses contain unknown scenarios: "
            + ", ".join(sorted(statuses))
        )
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "registry_id": registry.registry_id,
        "scenarios": rows,
    }


def render_markdown(summary: dict[str, object]) -> str:
    lines = [
        "# Historical crypto campaign",
        "",
        f"Registry: `{summary['registry_id']}`",
        "",
        "| Scenario | Pair | Timerange | Regime | Conclusion |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in summary["scenarios"]:
        lines.append(
            "| {scenario_id} | {pair} | {timerange} | {regime_label} | "
            "{conclusion} |".format(**row)
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix_parser = subparsers.add_parser("matrix")
    matrix_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    matrix_parser.add_argument("--output", type=Path)

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    summary_parser.add_argument("--status", action="append", type=Path, default=[])
    summary_parser.add_argument("--status-dir", type=Path)
    summary_parser.add_argument("--output", required=True, type=Path)
    summary_parser.add_argument("--markdown", required=True, type=Path)

    args = parser.parse_args()
    registry = load_registry(args.registry)
    if args.command == "matrix":
        rendered = matrix_json(registry)
        if args.output is None:
            print(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        return

    status_paths = list(args.status)
    if args.status_dir is not None and args.status_dir.exists():
        status_paths.extend(args.status_dir.rglob("scenario-status.json"))
    summary = build_summary(registry, status_paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
