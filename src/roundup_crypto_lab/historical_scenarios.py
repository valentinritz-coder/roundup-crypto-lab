"""Versioned historical crypto scenario registry parsing and validation."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from roundup_crypto_lab.investment_plan import InvestmentPlan

SCHEMA_VERSION = "historical-crypto-scenarios/v1"
DEFAULT_REGISTRY = Path("config/historical-crypto-scenarios-v1.json")
SUPPORTED_PAIRS = frozenset({"BTC/EUR", "ETH/EUR"})
SUPPORTED_TIMEFRAMES = frozenset({"4h"})
SUPPORTED_CAPITAL_MODES = frozenset(
    {"one_shot_capital", "recurring_monthly_contributions"}
)
SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIMERANGE_PATTERN = re.compile(r"^(\d{8})-(\d{8})$")


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _parse_timerange(value: object) -> tuple[str, datetime, datetime]:
    timerange = _text(value, "timerange")
    match = TIMERANGE_PATTERN.fullmatch(timerange)
    if match is None:
        raise ValueError("timerange must use strict YYYYMMDD-YYYYMMDD format")
    try:
        start = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=UTC)
        end = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("timerange contains an invalid calendar date") from error
    if start >= end:
        raise ValueError("timerange start must be strictly before end")
    return timerange, start, end


@dataclass(frozen=True)
class HistoricalScenario:
    scenario_id: str
    pair: str
    timeframe: str
    timerange: str
    capital_mode: str
    initial_capital: Decimal
    monthly_budget: Decimal
    contribution_day: int
    fee_ratio: Decimal
    regime_label: str
    overlap_reason: str | None = None

    @classmethod
    def from_mapping(cls, value: object) -> HistoricalScenario:
        row = _mapping(value, "scenario")
        scenario_id = _text(row.get("scenario_id"), "scenario id")
        if SCENARIO_ID_PATTERN.fullmatch(scenario_id) is None:
            raise ValueError("scenario id must be lowercase kebab-case")
        pair = _text(row.get("pair"), "pair")
        if pair not in SUPPORTED_PAIRS:
            raise ValueError(f"unsupported pair: {pair}")
        timeframe = _text(row.get("timeframe"), "timeframe")
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        timerange, _, _ = _parse_timerange(row.get("timerange"))
        capital_mode = _text(row.get("capital_mode"), "capital mode")
        if capital_mode not in SUPPORTED_CAPITAL_MODES:
            raise ValueError(f"unsupported capital mode: {capital_mode}")
        contribution_day = row.get("contribution_day")
        if isinstance(contribution_day, bool) or not isinstance(contribution_day, int):
            raise ValueError("contribution day must be an integer")
        plan = InvestmentPlan(
            row.get("initial_capital"),
            row.get("monthly_budget"),
            row.get("fee_ratio"),
            contribution_day,
        )
        regime_label = _text(row.get("regime_label"), "regime label")
        overlap_reason_value = row.get("overlap_reason")
        overlap_reason = None
        if overlap_reason_value is not None:
            overlap_reason = _text(overlap_reason_value, "overlap reason")
        return cls(
            scenario_id=scenario_id,
            pair=pair,
            timeframe=timeframe,
            timerange=timerange,
            capital_mode=capital_mode,
            initial_capital=plan.initial_capital,
            monthly_budget=plan.monthly_budget,
            contribution_day=plan.contribution_day,
            fee_ratio=plan.fee_ratio,
            regime_label=regime_label,
            overlap_reason=overlap_reason,
        )

    def canonical_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "scenario_id": self.scenario_id,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "timerange": self.timerange,
            "capital_mode": self.capital_mode,
            "initial_capital": _canonical_decimal(self.initial_capital),
            "monthly_budget": _canonical_decimal(self.monthly_budget),
            "contribution_day": self.contribution_day,
            "fee_ratio": _canonical_decimal(self.fee_ratio),
            "regime_label": self.regime_label,
        }
        if self.overlap_reason is not None:
            result["overlap_reason"] = self.overlap_reason
        return result

    def bounds(self) -> tuple[datetime, datetime]:
        _, start, end = _parse_timerange(self.timerange)
        return start, end


@dataclass(frozen=True)
class HistoricalScenarioRegistry:
    registry_id: str
    frozen_at_utc: str
    selection_policy: str
    scenarios: tuple[HistoricalScenario, ...]

    @classmethod
    def from_mapping(cls, value: object) -> HistoricalScenarioRegistry:
        payload = _mapping(value, "registry")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported historical scenario registry schema")
        registry_id = _text(payload.get("registry_id"), "registry id")
        frozen_at_utc = _text(payload.get("frozen_at_utc"), "frozen timestamp")
        try:
            frozen = datetime.fromisoformat(frozen_at_utc.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("frozen timestamp must be valid ISO-8601") from error
        if frozen.tzinfo is None:
            raise ValueError("frozen timestamp must be timezone-aware")
        selection_policy = _text(payload.get("selection_policy"), "selection policy")
        raw_scenarios = payload.get("scenarios")
        if not isinstance(raw_scenarios, list) or not raw_scenarios:
            raise ValueError("scenarios must be a non-empty list")
        scenarios = tuple(HistoricalScenario.from_mapping(row) for row in raw_scenarios)
        _validate_scenarios(scenarios)
        return cls(
            registry_id=registry_id,
            frozen_at_utc=frozen.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            selection_policy=selection_policy,
            scenarios=tuple(sorted(scenarios, key=lambda item: item.scenario_id)),
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "registry_id": self.registry_id,
            "frozen_at_utc": self.frozen_at_utc,
            "selection_policy": self.selection_policy,
            "scenarios": [scenario.canonical_mapping() for scenario in self.scenarios],
        }


def _validate_scenarios(scenarios: tuple[HistoricalScenario, ...]) -> None:
    identifiers: set[str] = set()
    identities: set[tuple[object, ...]] = set()
    for scenario in scenarios:
        if scenario.scenario_id in identifiers:
            raise ValueError(f"duplicate scenario id: {scenario.scenario_id}")
        identifiers.add(scenario.scenario_id)
        identity = (
            scenario.pair,
            scenario.timeframe,
            scenario.timerange,
            scenario.capital_mode,
            scenario.initial_capital,
            scenario.monthly_budget,
            scenario.contribution_day,
            scenario.fee_ratio,
        )
        if identity in identities:
            raise ValueError("duplicate scenario definition")
        identities.add(identity)
    for index, left in enumerate(scenarios):
        left_start, left_end = left.bounds()
        for right in scenarios[index + 1 :]:
            if (left.pair, left.timeframe, left.capital_mode) != (
                right.pair,
                right.timeframe,
                right.capital_mode,
            ):
                continue
            right_start, right_end = right.bounds()
            overlaps = max(left_start, right_start) < min(left_end, right_end)
            if overlaps and not (left.overlap_reason and right.overlap_reason):
                raise ValueError(
                    "overlapping comparable scenarios require overlap_reason on both rows"
                )


def load_registry(path: Path = DEFAULT_REGISTRY) -> HistoricalScenarioRegistry:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return HistoricalScenarioRegistry.from_mapping(payload)


def canonical_json(registry: HistoricalScenarioRegistry) -> str:
    return json.dumps(
        registry.canonical_mapping(),
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = canonical_json(load_registry(args.registry))
    if args.output is None:
        print(rendered, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
