"""Strict, deterministic registry for versioned DCA strategy configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

REGISTRY_SCHEMA_VERSION = 1
SUPPORTED_DECISION_CADENCES = frozenset(
    {"every_candle", "funding_event", "daily", "weekly", "monthly"}
)
RESEARCH_STATUSES = frozenset({"baseline", "exploratory", "preregistered", "confirmed"})
MINIMUM_ORDER_BEHAVIORS = frozenset({"skip", "spend_available"})

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class ParameterSpec:
    """Code-owned validation rule for one implementation parameter."""

    kind: str
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None


@dataclass(frozen=True)
class ImplementationSpec:
    """Code-owned implementation identity and configuration contract."""

    identity: str
    decision_cadences: frozenset[str]
    parameters: Mapping[str, ParameterSpec]
    required_indicator_names: tuple[str, ...] = ()
    ordered_thresholds: tuple[tuple[str, str], ...] = ()


_FRACTION = ParameterSpec("decimal", minimum=Decimal("0"), maximum=Decimal("1"))
_POSITIVE_MULTIPLIER = ParameterSpec(
    "decimal", minimum=Decimal("0"), maximum=Decimal("4")
)

IMPLEMENTATION_SPECS: Mapping[str, ImplementationSpec] = MappingProxyType(
    {
        "fixed_daily": ImplementationSpec(
            identity="roundup_crypto_lab.dca.fixed_daily@1",
            decision_cadences=frozenset({"daily"}),
            parameters=MappingProxyType({}),
        ),
        "fixed_weekly": ImplementationSpec(
            identity="roundup_crypto_lab.dca.fixed_weekly@1",
            decision_cadences=frozenset({"weekly"}),
            parameters=MappingProxyType(
                {"weekday": ParameterSpec("integer", minimum=0, maximum=6)}
            ),
        ),
        "fixed_monthly": ImplementationSpec(
            identity="roundup_crypto_lab.dca.fixed_monthly@1",
            decision_cadences=frozenset({"monthly"}),
            parameters=MappingProxyType({}),
        ),
        "fixed_periodic": ImplementationSpec(
            identity="roundup_crypto_lab.dca.fixed_periodic@1",
            decision_cadences=frozenset({"monthly"}),
            parameters=MappingProxyType(
                {
                    "interval_months": ParameterSpec(
                        "integer", minimum=1, maximum=3
                    ),
                    "phase_offset_months": ParameterSpec(
                        "integer", minimum=0, maximum=2
                    ),
                }
            ),
        ),
        "immediate_floor_drawdown_reserve": ImplementationSpec(
            identity="roundup_crypto_lab.dca.immediate_floor_drawdown_reserve@1",
            decision_cadences=frozenset({"every_candle"}),
            parameters=MappingProxyType(
                {
                    "immediate_floor_fraction": _FRACTION,
                    "tier_1_drawdown": _FRACTION,
                    "tier_1_release_fraction": _FRACTION,
                    "tier_2_drawdown": _FRACTION,
                    "tier_2_release_fraction": _FRACTION,
                    "tier_3_drawdown": _FRACTION,
                    "tier_3_release_fraction": _FRACTION,
                }
            ),
            required_indicator_names=("rolling_drawdown",),
            ordered_thresholds=(
                ("tier_1_drawdown", "tier_2_drawdown"),
                ("tier_2_drawdown", "tier_3_drawdown"),
                ("tier_1_release_fraction", "tier_2_release_fraction"),
                ("tier_2_release_fraction", "tier_3_release_fraction"),
            ),
        ),
        "no_sell_value_averaging": ImplementationSpec(
            identity="roundup_crypto_lab.dca.no_sell_value_averaging@1",
            decision_cadences=frozenset({"every_candle"}),
            parameters=MappingProxyType(
                {
                    "minimum_new_contribution_fraction": _FRACTION,
                    "target_value_multiplier": ParameterSpec(
                        "decimal", minimum=Decimal("0.5"), maximum=Decimal("2")
                    ),
                }
            ),
        ),
        "moving_average_deviation": ImplementationSpec(
            identity="roundup_crypto_lab.dca.moving_average_deviation@1",
            decision_cadences=frozenset({"daily"}),
            parameters=MappingProxyType(
                {
                    "above_ma_multiplier": _POSITIVE_MULTIPLIER,
                    "above_ma_threshold": _FRACTION,
                    "base_allocation_fraction": _FRACTION,
                    "below_ma_multiplier": _POSITIVE_MULTIPLIER,
                    "below_ma_threshold": _FRACTION,
                    "neutral_multiplier": _POSITIVE_MULTIPLIER,
                }
            ),
            required_indicator_names=("long_ma", "previous_close"),
        ),
        "ker_adx_accumulation": ImplementationSpec(
            identity="roundup_crypto_lab.dca.ker_adx_accumulation@1",
            decision_cadences=frozenset({"every_candle"}),
            parameters=MappingProxyType(
                {
                    "accelerated_release_fraction": _FRACTION,
                    "adx_threshold": ParameterSpec(
                        "decimal", minimum=Decimal("0"), maximum=Decimal("100")
                    ),
                    "immediate_floor_fraction": _FRACTION,
                    "ker_threshold": _FRACTION,
                }
            ),
            required_indicator_names=("adx_14", "ker_20"),
        ),
        # Registry-only fixture for proving strict indicator and threshold validation.
        # It is deliberately not dynamically imported or exposed as an executable strategy.
        "indicator_band_fixture": ImplementationSpec(
            identity="roundup_crypto_lab.dca.fixtures.indicator_band@1",
            decision_cadences=frozenset({"every_candle"}),
            parameters=MappingProxyType(
                {
                    "allocation_fraction": _FRACTION,
                    "lower_threshold": ParameterSpec("decimal"),
                    "upper_threshold": ParameterSpec("decimal"),
                }
            ),
            required_indicator_names=("signal",),
            ordered_thresholds=(("lower_threshold", "upper_threshold"),),
        ),
    }
)


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must match {_IDENTIFIER.pattern}")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    keys = set(value)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        raise ValueError(f"{name} is missing keys: {', '.join(missing)}")
    if extra:
        raise ValueError(f"{name} has unsupported keys: {', '.join(extra)}")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _integer(
    value: Any,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, not a boolean or decimal")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def _decimal_text(
    value: Any,
    name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{name} must be a decimal string or number, not a boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {_canonical_decimal(minimum)}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be at most {_canonical_decimal(maximum)}")
    return _canonical_decimal(result)


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _canonical_json_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{name} keys must be strings")
        return MappingProxyType(
            {
                key: _canonical_json_value(value[key], f"{name}.{key}")
                for key in sorted(value)
            }
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_canonical_json_value(item, f"{name}[]") for item in value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} decimal values must be finite")
        return _canonical_decimal(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(
        f"{name} must contain only objects, arrays, strings, integers, booleans, "
        "null or finite decimal values"
    )


def _artifact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _artifact_value(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_artifact_value(item) for item in value]
    return value


@dataclass(frozen=True)
class IndicatorRequirement:
    """Exact, non-importable identity of one causal indicator dependency."""

    name: str
    definition_id: str
    version: str
    warmup_candles: int
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class MinimumOrderRule:
    """Minimum gross order amount and deterministic below-minimum behavior."""

    amount: str
    behavior: str


@dataclass(frozen=True)
class StrategyDefinition:
    """Canonical configuration and provenance metadata for one DCA strategy."""

    strategy_id: str
    implementation: str
    implementation_identity: str
    strategy_version: str
    hypothesis: str
    decision_cadence: str
    required_indicators: tuple[IndicatorRequirement, ...]
    parameters: Mapping[str, str | int]
    maximum_pending_cash_age_days: int | None
    minimum_order: MinimumOrderRule
    research_status: str


@dataclass(frozen=True)
class StrategyRegistry:
    """Validated, deterministically ordered strategy registry."""

    registry_schema_version: int
    registry_id: str
    strategies: tuple[StrategyDefinition, ...]

    def strategy(self, strategy_id: str) -> StrategyDefinition:
        strategy_id = _identifier(strategy_id, "strategy id")
        for strategy in self.strategies:
            if strategy.strategy_id == strategy_id:
                return strategy
        raise ValueError(f"unknown strategy id: {strategy_id}")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "registry_schema_version": self.registry_schema_version,
            "registry_id": self.registry_id,
            "strategies": [_strategy_artifact(strategy) for strategy in self.strategies],
        }

    def canonical_payload_bytes(self) -> bytes:
        return _json_bytes(self.canonical_payload())

    @property
    def digest(self) -> str:
        return f"sha256:{hashlib.sha256(self.canonical_payload_bytes()).hexdigest()}"

    def canonical_document(self) -> dict[str, Any]:
        return {"registry": self.canonical_payload(), "registry_digest": self.digest}

    def canonical_document_bytes(self) -> bytes:
        return _json_bytes(self.canonical_document())


def _parse_indicator(value: Any, name: str) -> IndicatorRequirement:
    data = _mapping(value, name)
    _strict_keys(
        data,
        {"name", "definition_id", "version", "warmup_candles", "parameters"},
        name,
    )
    return IndicatorRequirement(
        name=_identifier(data["name"], f"{name}.name"),
        definition_id=_identifier(data["definition_id"], f"{name}.definition_id"),
        version=_identifier(data["version"], f"{name}.version"),
        warmup_candles=_integer(
            data["warmup_candles"], f"{name}.warmup_candles", minimum=0
        ),
        parameters=_canonical_json_value(
            _mapping(data["parameters"], f"{name}.parameters"), f"{name}.parameters"
        ),
    )


def _parse_minimum_order(value: Any, name: str) -> MinimumOrderRule:
    data = _mapping(value, name)
    _strict_keys(data, {"amount", "behavior"}, name)
    behavior = data["behavior"]
    if behavior not in MINIMUM_ORDER_BEHAVIORS:
        supported = ", ".join(sorted(MINIMUM_ORDER_BEHAVIORS))
        raise ValueError(f"{name}.behavior must be one of: {supported}")
    return MinimumOrderRule(
        amount=_decimal_text(data["amount"], f"{name}.amount", minimum=Decimal("0")),
        behavior=behavior,
    )


def _parse_parameters(
    value: Any,
    implementation: str,
    spec: ImplementationSpec,
    name: str,
) -> Mapping[str, str | int]:
    data = _mapping(value, name)
    expected = set(spec.parameters)
    _strict_keys(data, expected, name)
    parsed: dict[str, str | int] = {}
    for parameter_name in sorted(spec.parameters):
        parameter_spec = spec.parameters[parameter_name]
        raw = data[parameter_name]
        if parameter_spec.kind == "integer":
            minimum = parameter_spec.minimum
            maximum = parameter_spec.maximum
            parsed[parameter_name] = _integer(
                raw,
                f"{name}.{parameter_name}",
                minimum=None if minimum is None else int(minimum),
                maximum=None if maximum is None else int(maximum),
            )
        elif parameter_spec.kind == "decimal":
            parsed[parameter_name] = _decimal_text(
                raw,
                f"{name}.{parameter_name}",
                minimum=None
                if parameter_spec.minimum is None
                else Decimal(parameter_spec.minimum),
                maximum=None
                if parameter_spec.maximum is None
                else Decimal(parameter_spec.maximum),
            )
        else:
            raise RuntimeError(
                f"unsupported code-owned parameter kind for {implementation}: "
                f"{parameter_spec.kind}"
            )
    if implementation == "fixed_periodic":
        interval = int(parsed["interval_months"])
        phase = int(parsed["phase_offset_months"])
        if phase >= interval:
            raise ValueError(
                f"{name}.phase_offset_months must be lower than interval_months"
            )
    for lower_name, upper_name in spec.ordered_thresholds:
        lower = Decimal(str(parsed[lower_name]))
        upper = Decimal(str(parsed[upper_name]))
        if lower >= upper:
            raise ValueError(
                f"{name}.{lower_name} must be strictly lower than {name}.{upper_name}"
            )
    return MappingProxyType(parsed)


def _parse_strategy(value: Any, index: int) -> StrategyDefinition:
    name = f"strategies[{index}]"
    data = _mapping(value, name)
    _strict_keys(
        data,
        {
            "strategy_id",
            "implementation",
            "strategy_version",
            "hypothesis",
            "decision_cadence",
            "required_indicators",
            "parameters",
            "maximum_pending_cash_age_days",
            "minimum_order",
            "research_status",
        },
        name,
    )
    strategy_id = _identifier(data["strategy_id"], f"{name}.strategy_id")
    implementation = _identifier(data["implementation"], f"{name}.implementation")
    spec = IMPLEMENTATION_SPECS.get(implementation)
    if spec is None:
        raise ValueError(f"{name}.implementation is unknown: {implementation}")

    hypothesis = data["hypothesis"]
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError(f"{name}.hypothesis must be a non-empty string")
    hypothesis = hypothesis.strip()

    cadence = data["decision_cadence"]
    if cadence not in SUPPORTED_DECISION_CADENCES:
        supported = ", ".join(sorted(SUPPORTED_DECISION_CADENCES))
        raise ValueError(f"{name}.decision_cadence must be one of: {supported}")
    if cadence not in spec.decision_cadences:
        raise ValueError(
            f"{name}.decision_cadence {cadence!r} is unsupported by {implementation}"
        )

    indicators = tuple(
        _parse_indicator(item, f"{name}.required_indicators[{position}]")
        for position, item in enumerate(
            _sequence(data["required_indicators"], f"{name}.required_indicators")
        )
    )
    indicator_names = [indicator.name for indicator in indicators]
    if len(set(indicator_names)) != len(indicator_names):
        raise ValueError(f"{name}.required_indicators contains duplicate names")
    indicators = tuple(sorted(indicators, key=lambda indicator: indicator.name))
    if tuple(indicator.name for indicator in indicators) != spec.required_indicator_names:
        expected = ", ".join(spec.required_indicator_names) or "none"
        raise ValueError(
            f"{name}.required_indicators must define exactly the indicators: {expected}"
        )

    maximum_age = data["maximum_pending_cash_age_days"]
    if maximum_age is not None:
        maximum_age = _integer(
            maximum_age,
            f"{name}.maximum_pending_cash_age_days",
            minimum=0,
        )

    research_status = data["research_status"]
    if research_status not in RESEARCH_STATUSES:
        supported = ", ".join(sorted(RESEARCH_STATUSES))
        raise ValueError(f"{name}.research_status must be one of: {supported}")

    return StrategyDefinition(
        strategy_id=strategy_id,
        implementation=implementation,
        implementation_identity=spec.identity,
        strategy_version=_identifier(data["strategy_version"], f"{name}.strategy_version"),
        hypothesis=hypothesis,
        decision_cadence=cadence,
        required_indicators=indicators,
        parameters=_parse_parameters(
            data["parameters"], implementation, spec, f"{name}.parameters"
        ),
        maximum_pending_cash_age_days=maximum_age,
        minimum_order=_parse_minimum_order(data["minimum_order"], f"{name}.minimum_order"),
        research_status=research_status,
    )


def parse_registry(value: Any) -> StrategyRegistry:
    """Validate and canonicalize one decoded registry document."""
    data = _mapping(value, "registry")
    _strict_keys(data, {"registry_schema_version", "registry_id", "strategies"}, "registry")
    schema_version = _integer(
        data["registry_schema_version"], "registry.registry_schema_version", minimum=1
    )
    if schema_version != REGISTRY_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported registry schema version {schema_version}; "
            f"runtime supports {REGISTRY_SCHEMA_VERSION}"
        )
    strategies = tuple(
        _parse_strategy(item, index)
        for index, item in enumerate(_sequence(data["strategies"], "registry.strategies"))
    )
    identifiers = [strategy.strategy_id for strategy in strategies]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("registry contains duplicate strategy ids")
    return StrategyRegistry(
        registry_schema_version=schema_version,
        registry_id=_identifier(data["registry_id"], "registry.registry_id"),
        strategies=tuple(sorted(strategies, key=lambda strategy: strategy.strategy_id)),
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"registry JSON must not contain {value}")


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"registry JSON contains duplicate object key: {key}")
        result[key] = value
    return result


def loads_registry(text: str) -> StrategyRegistry:
    """Parse strict JSON, rejecting duplicate keys and non-finite constants."""
    try:
        decoded = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid registry JSON: {exc.msg}") from exc
    return parse_registry(decoded)


def load_registry(path: Path) -> StrategyRegistry:
    """Load a UTF-8 JSON registry from disk."""
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_file():
        raise ValueError(f"registry file does not exist: {path}")
    return loads_registry(path.read_text(encoding="utf-8"))


def strategy_provenance(
    registry: StrategyRegistry,
    strategy_id: str,
    repository_commit: str,
) -> dict[str, Any]:
    """Build sufficient immutable provenance to reproduce one configured strategy run."""
    if not isinstance(registry, StrategyRegistry):
        raise TypeError("registry must be a StrategyRegistry")
    if not isinstance(repository_commit, str) or not _COMMIT.fullmatch(repository_commit):
        raise ValueError("repository commit must be an exact 40-character hexadecimal SHA")
    strategy = registry.strategy(strategy_id)
    return {
        "registry_schema_version": registry.registry_schema_version,
        "registry_id": registry.registry_id,
        "registry_digest": registry.digest,
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "implementation": strategy.implementation,
        "implementation_identity": strategy.implementation_identity,
        "parameters": _artifact_value(strategy.parameters),
        "indicator_definitions": [
            {
                "name": indicator.name,
                "definition_id": indicator.definition_id,
                "version": indicator.version,
                "warmup_candles": indicator.warmup_candles,
                "parameters": _artifact_value(indicator.parameters),
            }
            for indicator in strategy.required_indicators
        ],
        "repository_commit": repository_commit.lower(),
    }


def _strategy_artifact(strategy: StrategyDefinition) -> dict[str, Any]:
    return {
        "strategy_id": strategy.strategy_id,
        "implementation": strategy.implementation,
        "implementation_identity": strategy.implementation_identity,
        "strategy_version": strategy.strategy_version,
        "hypothesis": strategy.hypothesis,
        "decision_cadence": strategy.decision_cadence,
        "required_indicators": [
            {
                "name": indicator.name,
                "definition_id": indicator.definition_id,
                "version": indicator.version,
                "warmup_candles": indicator.warmup_candles,
                "parameters": _artifact_value(indicator.parameters),
            }
            for indicator in strategy.required_indicators
        ],
        "parameters": _artifact_value(strategy.parameters),
        "maximum_pending_cash_age_days": strategy.maximum_pending_cash_age_days,
        "minimum_order": {
            "amount": strategy.minimum_order.amount,
            "behavior": strategy.minimum_order.behavior,
        },
        "research_status": strategy.research_status,
    }


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    """Validate a registry and emit its canonical JSON document and digest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    registry = load_registry(args.registry)
    output = registry.canonical_document_bytes() + b"\n"
    if args.output is None:
        sys.stdout.buffer.write(output)
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)


if __name__ == "__main__":
    main()
