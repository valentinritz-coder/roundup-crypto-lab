"""Controls and assertions for the narrow native-Freqtrade differential suite.

This module intentionally does not describe the cash-flow adapter as generally
Freqtrade-equivalent.  It records the exact single-pair configuration used by
the offline fixture and compares only the lifecycle fields the adapter owns.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SUPPORTED_PAIRS = frozenset({"BTC/EUR", "ETH/EUR"})


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def config_digest(config: Mapping[str, Any]) -> str:
    """Return the stable digest recorded with a generated execution config."""
    return hashlib.sha256(_canonical_json(config)).hexdigest()


def generate_single_pair_config(
    source: Path, destination: Path, pair: str, *, overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Copy a committed config into a temporary, auditable one-pair config."""
    if pair not in SUPPORTED_PAIRS:
        raise ValueError("only BTC/EUR and ETH/EUR are supported")
    config = json.loads(source.read_text(encoding="utf-8"))
    exchange = config.get("exchange")
    if not isinstance(exchange, dict):
        raise ValueError("config has no exchange mapping")
    generated = dict(config)
    generated_exchange = dict(exchange)
    generated_exchange["pair_whitelist"] = [pair]
    generated["exchange"] = generated_exchange
    generated.update(overrides or {})
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "selected_pair": pair,
        "config_digest": config_digest(generated),
        "generated_config": str(destination),
        "timeframe": generated.get("timeframe"),
    }


def validate_execution_scope(
    *, pair: str, data_file: Path, strategy_timeframe: str, config: Mapping[str, Any]
) -> None:
    """Require adapter data, strategy metadata, and native whitelist to agree."""
    if pair not in SUPPORTED_PAIRS:
        raise ValueError("only BTC/EUR and ETH/EUR are supported")
    if data_file.name != f"{pair.replace('/', '_')}-4h.feather":
        raise ValueError(f"data file must be {pair.replace('/', '_')}-4h.feather for {pair}")
    if strategy_timeframe != config.get("timeframe"):
        raise ValueError("strategy timeframe and native config timeframe differ")
    whitelist = config.get("exchange", {}).get("pair_whitelist", [])
    if whitelist != [pair]:
        raise ValueError("native config must contain exactly the selected pair")


LIFECYCLE_FIELDS = (
    "entry_timestamp",
    "exit_timestamp",
    "entry_price",
    "exit_price",
    "entry_gross_stake",
    "quantity",
    "entry_fee",
    "exit_fee",
    "exit_reason",
)


def assert_lifecycle_equivalent(
    native: Sequence[Mapping[str, Any]],
    adapter: Sequence[Mapping[str, Any]],
    *,
    quantity_tolerance: Decimal = Decimal("0.00000001"),
    derived_monetary_tolerance: Decimal = Decimal("0.00000001"),
) -> None:
    """Reject every lifecycle mismatch, allowing only explicit quantity rounding."""
    if len(native) != len(adapter):
        raise AssertionError(f"trade count differs: native={len(native)}, adapter={len(adapter)}")
    for index, (expected, actual) in enumerate(zip(native, adapter, strict=True), start=1):
        for field in LIFECYCLE_FIELDS:
            if field == "quantity":
                if (
                    abs(Decimal(str(expected[field])) - Decimal(str(actual[field])))
                    > quantity_tolerance
                ):
                    raise AssertionError(f"trade {index} quantity differs")
            elif field in {"entry_fee", "exit_fee"}:
                if (
                    abs(Decimal(str(expected[field])) - Decimal(str(actual[field])))
                    > derived_monetary_tolerance
                ):
                    raise AssertionError(
                        f"trade {index} {field} differs: native={expected[field]!r}, "
                        f"adapter={actual[field]!r}"
                    )
            elif field in {"entry_price", "exit_price", "entry_gross_stake"}:
                if Decimal(str(expected[field])) != Decimal(str(actual[field])):
                    raise AssertionError(
                        f"trade {index} {field} differs: native={expected[field]!r}, "
                        f"adapter={actual[field]!r}"
                    )
            elif str(expected[field]) != str(actual[field]):
                raise AssertionError(
                    f"trade {index} {field} differs: native={expected[field]!r}, "
                    f"adapter={actual[field]!r}"
                )


def assert_final_balances_equivalent(
    native: Mapping[str, Any],
    adapter: Mapping[str, Any],
    *,
    tolerance: Decimal = Decimal("0.00000001"),
) -> None:
    """Compare the final cash, crypto mark, and equity with no implicit tolerance."""
    for field in ("free_cash", "crypto_value", "final_equity"):
        if abs(Decimal(str(native[field])) - Decimal(str(adapter[field]))) > tolerance:
            raise AssertionError(
                f"final {field} differs: native={native[field]!r}, adapter={adapter[field]!r}"
            )


def normalize_adapter_result_for_native_comparison(adapter: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize adapter fields to Freqtrade's documented eight-decimal export form."""
    trades = adapter.get("trades")
    if not isinstance(trades, list):
        raise ValueError("adapter result has no trade ledger")
    normalized = []
    for trade in trades:
        if not isinstance(trade, Mapping):
            raise ValueError("adapter trade is invalid")
        item = dict(trade)
        try:
            item["entry_gross_stake"] = Decimal(str(item["entry_gross_stake"])).quantize(
                Decimal("0.00000001")
            )
        except (InvalidOperation, KeyError) as error:
            raise ValueError("adapter trade has invalid stake") from error
        for field in ("entry_timestamp", "exit_timestamp"):
            if item.get(field) is not None:
                item[field] = str(item[field]).replace(" ", "T")
        reason = item.get("exit_reason")
        if reason == "close_below_sma20":
            item["exit_reason"] = "exit_signal"
        elif reason == "trailing_stop_loss":
            item["exit_reason"] = "stop_loss"
        normalized.append(item)
    return {
        "trades": normalized,
        **{key: adapter[key] for key in ("free_cash", "crypto_value", "final_equity")},
    }
