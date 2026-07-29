"""Strict, versioned execution-cost profiles for DCA simulations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from roundup_crypto_lab.dca_decimal_safety import canonical_decimal
from roundup_crypto_lab.deployment_engine import purchase as deployment_purchase
from roundup_crypto_lab.investment_plan import CashFlowEvent

COST_PROFILE_SCHEMA_VERSION = "execution-cost-profile/v1"
DEFAULT_COST_PROFILE_DIR = Path("config/execution-cost-profiles")
PROFILE_KINDS = frozenset(
    {"control", "baseline", "research_assumption", "sensitivity"}
)
BELOW_MINIMUM_BEHAVIORS = frozenset({"carry_forward"})


def _decimal(
    value: object,
    name: str,
    *,
    minimum: Decimal = Decimal("0"),
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    if result < minimum:
        raise ValueError(f"{name} must be at least {canonical_decimal(minimum)}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be at most {canonical_decimal(maximum)}")
    return result


def _strict_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        raise ValueError(f"{name} is missing keys: {', '.join(missing)}")
    if extra:
        raise ValueError(f"{name} has unsupported keys: {', '.join(extra)}")


@dataclass(frozen=True)
class ExecutionCostProfile:
    """Immutable execution assumptions with exact Decimal accounting."""

    cost_profile_id: str
    profile_version: int
    description: str
    profile_kind: str
    trading_fee_ratio: Decimal
    half_spread_ratio: Decimal
    fixed_order_fee: Decimal
    minimum_order_amount: Decimal
    below_minimum_behavior: str

    def __post_init__(self) -> None:
        identifier = self.cost_profile_id
        if (
            not isinstance(identifier, str)
            or not identifier
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-._"
                for character in identifier
            )
        ):
            raise ValueError("cost_profile_id must be a lowercase stable identifier")
        if isinstance(self.profile_version, bool) or not isinstance(self.profile_version, int):
            raise ValueError("profile_version must be an integer")
        if self.profile_version < 1:
            raise ValueError("profile_version must be at least 1")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be non-empty")
        if self.profile_kind not in PROFILE_KINDS:
            raise ValueError(
                "profile_kind must be one of: " + ", ".join(sorted(PROFILE_KINDS))
            )
        object.__setattr__(
            self,
            "trading_fee_ratio",
            _decimal(
                self.trading_fee_ratio,
                "trading_fee_ratio",
                maximum=Decimal("0.999999999999999999"),
            ),
        )
        object.__setattr__(
            self,
            "half_spread_ratio",
            _decimal(
                self.half_spread_ratio,
                "half_spread_ratio",
                maximum=Decimal("0.10"),
            ),
        )
        object.__setattr__(
            self,
            "fixed_order_fee",
            _decimal(self.fixed_order_fee, "fixed_order_fee"),
        )
        object.__setattr__(
            self,
            "minimum_order_amount",
            _decimal(self.minimum_order_amount, "minimum_order_amount"),
        )
        if self.below_minimum_behavior not in BELOW_MINIMUM_BEHAVIORS:
            raise ValueError(
                "below_minimum_behavior must be one of: "
                + ", ".join(sorted(BELOW_MINIMUM_BEHAVIORS))
            )
        if self.fixed_order_fee > 0 and self.profile_kind != "sensitivity":
            raise ValueError(
                "fixed_order_fee is supported only by explicitly labelled sensitivity profiles"
            )
        if self.fixed_order_fee > 0:
            break_even = self.fixed_order_fee / (
                Decimal("1") - self.trading_fee_ratio
            )
            if self.minimum_order_amount <= break_even:
                raise ValueError(
                    "minimum_order_amount must exceed the fixed-fee break-even amount"
                )

    @property
    def explicit_profile_id(self) -> str:
        return f"{self.cost_profile_id}@{self.profile_version}"

    def can_execute(self, gross_amount: Decimal | str) -> bool:
        amount = _decimal(gross_amount, "gross_amount")
        if amount < self.minimum_order_amount:
            return False
        explicit = amount * self.trading_fee_ratio + self.fixed_order_fee
        return amount > explicit

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": COST_PROFILE_SCHEMA_VERSION,
            "cost_profile_id": self.cost_profile_id,
            "profile_version": self.profile_version,
            "description": self.description.strip(),
            "profile_kind": self.profile_kind,
            "trading_fee_ratio": canonical_decimal(self.trading_fee_ratio),
            "half_spread_ratio": canonical_decimal(self.half_spread_ratio),
            "fixed_order_fee": canonical_decimal(self.fixed_order_fee),
            "minimum_order_amount": canonical_decimal(self.minimum_order_amount),
            "below_minimum_behavior": self.below_minimum_behavior,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def artifact(self) -> dict[str, Any]:
        return {
            **self.canonical_payload(),
            "profile_digest": self.digest,
        }


def parse_cost_profile(value: object) -> ExecutionCostProfile:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("execution cost profile must be a JSON object")
    expected = {
        "schema_version",
        "cost_profile_id",
        "profile_version",
        "description",
        "profile_kind",
        "trading_fee_ratio",
        "half_spread_ratio",
        "fixed_order_fee",
        "minimum_order_amount",
        "below_minimum_behavior",
    }
    _strict_keys(value, expected, "execution cost profile")
    if value["schema_version"] != COST_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported execution cost profile schema: {value['schema_version']}"
        )
    return ExecutionCostProfile(
        cost_profile_id=value["cost_profile_id"],
        profile_version=value["profile_version"],
        description=value["description"],
        profile_kind=value["profile_kind"],
        trading_fee_ratio=value["trading_fee_ratio"],
        half_spread_ratio=value["half_spread_ratio"],
        fixed_order_fee=value["fixed_order_fee"],
        minimum_order_amount=value["minimum_order_amount"],
        below_minimum_behavior=value["below_minimum_behavior"],
    )


def loads_cost_profile(text: str) -> ExecutionCostProfile:
    def reject_constant(value: str) -> None:
        raise ValueError(f"execution cost profile must not contain {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"execution cost profile contains duplicate object key: {key}"
                )
            result[key] = value
        return result

    try:
        decoded = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid execution cost profile JSON: {exc.msg}") from exc
    return parse_cost_profile(decoded)


def load_cost_profile(path: Path | str) -> ExecutionCostProfile:
    profile_path = Path(path)
    if not profile_path.is_file():
        raise ValueError(f"execution cost profile file does not exist: {profile_path}")
    return loads_cost_profile(profile_path.read_text(encoding="utf-8"))


def legacy_fee_profile(fee_ratio: Decimal | str) -> ExecutionCostProfile:
    return ExecutionCostProfile(
        cost_profile_id="legacy-fee-only-v1",
        profile_version=1,
        description=(
            "Backward-compatible projection of the legacy proportional fee input "
            "with no spread, fixed fee or minimum order."
        ),
        profile_kind="baseline",
        trading_fee_ratio=fee_ratio,
        half_spread_ratio="0",
        fixed_order_fee="0",
        minimum_order_amount="0",
        below_minimum_behavior="carry_forward",
    )


def resolve_cost_profile(
    reference: Path | str | None,
    *,
    legacy_fee_ratio: Decimal | str | None = None,
    search_dir: Path = DEFAULT_COST_PROFILE_DIR,
) -> ExecutionCostProfile:
    if reference is not None and legacy_fee_ratio is not None:
        raise ValueError("--cost-profile and --fee are mutually exclusive")
    if reference is None:
        if legacy_fee_ratio is None:
            raise ValueError("either a cost profile or legacy fee ratio is required")
        return legacy_fee_profile(legacy_fee_ratio)

    candidate = Path(reference)
    if candidate.is_file():
        return load_cost_profile(candidate)
    if candidate.suffix or candidate.parent != Path("."):
        raise ValueError(f"execution cost profile file does not exist: {candidate}")
    resolved = search_dir / f"{candidate.name}.json"
    if not resolved.is_file():
        raise ValueError(
            f"unknown execution cost profile {candidate.name!r} in {search_dir}"
        )
    profile = load_cost_profile(resolved)
    if profile.cost_profile_id != candidate.name:
        raise ValueError(
            "execution cost profile filename identifier does not match cost_profile_id"
        )
    return profile


def execute_costed_purchase(
    candles: Any,
    event: CashFlowEvent,
    scheduled_at: Any,
    gross_amount: Decimal,
    profile: ExecutionCostProfile,
) -> dict[str, Any] | None:
    """Execute one buy with exact explicit-fee and spread decomposition."""
    if not isinstance(profile, ExecutionCostProfile):
        raise TypeError("profile must be an ExecutionCostProfile")
    gross = _decimal(gross_amount, "gross_amount")
    if not profile.can_execute(gross):
        raise ValueError("gross amount is below the executable cost-profile minimum")

    executed = deployment_purchase(
        candles,
        event,
        scheduled_at,
        gross,
        Decimal("0"),
    )
    if executed is None:
        return None

    reference_price = Decimal(str(executed["execution_price"]))
    execution_price = reference_price * (
        Decimal("1") + profile.half_spread_ratio
    )
    provisional_trading_fee = gross * profile.trading_fee_ratio
    fixed_fee = profile.fixed_order_fee
    net_notional = gross - provisional_trading_fee - fixed_fee
    trading_fee = gross - fixed_fee - net_notional
    explicit_fees = trading_fee + fixed_fee
    quantity = net_notional / execution_price
    if profile.half_spread_ratio == 0:
        spread_cost = Decimal("0")
    else:
        reference_notional = quantity * reference_price
        spread_cost = net_notional - reference_notional
        if spread_cost < 0:
            raise ValueError("spread accounting produced a negative implicit cost")

    executed.update(
        {
            "cost_profile_id": profile.cost_profile_id,
            "cost_profile_version": profile.profile_version,
            "reference_price": reference_price,
            "execution_price": execution_price,
            "trading_fee_paid": trading_fee,
            "fixed_order_fee_paid": fixed_fee,
            "fee_paid": explicit_fees,
            "net_contribution": net_notional,
            "net_notional": net_notional,
            "quantity": quantity,
            "estimated_spread_cost": spread_cost,
        }
    )
    return executed


def execution_cost_summary(
    purchases: list[dict[str, Any]],
    profile: ExecutionCostProfile,
) -> dict[str, Any]:
    trading_fees = sum(
        (Decimal(str(row.get("trading_fee_paid", row["fee_paid"]))) for row in purchases),
        Decimal("0"),
    )
    fixed_fees = sum(
        (Decimal(str(row.get("fixed_order_fee_paid", "0"))) for row in purchases),
        Decimal("0"),
    )
    spread_cost = sum(
        (Decimal(str(row.get("estimated_spread_cost", "0"))) for row in purchases),
        Decimal("0"),
    )
    explicit_fees = trading_fees + fixed_fees
    total_cost = explicit_fees + spread_cost
    gross_orders = sum(
        (Decimal(str(row["gross_contribution"])) for row in purchases),
        Decimal("0"),
    )
    average_order = (
        Decimal("0") if not purchases else gross_orders / Decimal(len(purchases))
    )
    return {
        "cost_profile": profile.artifact(),
        "order_count": len(purchases),
        "gross_order_total": canonical_decimal(gross_orders),
        "average_order_size": canonical_decimal(average_order),
        "trading_fees_paid": canonical_decimal(trading_fees),
        "fixed_order_fees_paid": canonical_decimal(fixed_fees),
        "explicit_fees_paid": canonical_decimal(explicit_fees),
        "estimated_spread_cost": canonical_decimal(spread_cost),
        "total_execution_cost": canonical_decimal(total_cost),
    }


def enrich_result_with_execution_costs(
    result: dict[str, Any],
    purchases: list[dict[str, Any]],
    profile: ExecutionCostProfile,
) -> dict[str, Any]:
    result["execution_costs"] = execution_cost_summary(purchases, profile)
    return result
