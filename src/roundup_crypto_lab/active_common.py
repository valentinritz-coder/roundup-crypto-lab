"""Versioned, validated active cash-flow artifacts and controlled reporting."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA_VERSION = "active-strategy-result/v1"
EXITS = frozenset({"exit_signal", "stop_loss"})
CAPITAL_MODES = frozenset({"one_shot_capital", "recurring_monthly_contributions"})
OPEN_POSITION_STATES = frozenset({"closed", "open_marked_at_final_close"})


def dec(value: object, name: str) -> Decimal:
    """Parse one finite Decimal from an external artifact field."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be decimal") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def ts(value: object, name: str) -> datetime:
    """Parse one timezone-aware timestamp and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _rows(value: object, name: str, *, nonempty: bool = False) -> list[object]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{name} must be {'a non-empty ' if nonempty else 'a '}list")
    return value


def _positive(value: object, name: str) -> Decimal:
    number = dec(value, name)
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    return number


def _nonnegative(value: object, name: str) -> Decimal:
    number = dec(value, name)
    if number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number


def identity(experiment: dict[str, object]) -> str:
    """Return the canonical cross-strategy experiment identity."""
    keys = (
        "selected_pair",
        "timeframe",
        "timerange",
        "start",
        "end",
        "capital_mode",
        "investment_plan",
        "effective_settings",
        "execution_model",
        "execution_scope",
    )
    canonical = {key: experiment.get(key) for key in keys}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
