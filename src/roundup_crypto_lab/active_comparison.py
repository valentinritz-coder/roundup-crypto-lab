"""Versioned active cash-flow artifacts and controlled comparison entry point."""

from roundup_crypto_lab.active_builder import build_active_result
from roundup_crypto_lab.active_common import (
    CAPITAL_MODES,
    EXITS,
    OPEN_POSITION_STATES,
    SCHEMA_VERSION,
    dec,
    identity,
    ts,
)
from roundup_crypto_lab.active_cross_validation import (
    DIFFERENTIAL_SCHEMA_VERSION,
    validate_differential,
    validate_native_metadata,
    validate_result_set,
)
from roundup_crypto_lab.active_reporting import main, render_summary
from roundup_crypto_lab.active_result_validation import validate_active_result

__all__ = [
    "CAPITAL_MODES",
    "DIFFERENTIAL_SCHEMA_VERSION",
    "EXITS",
    "OPEN_POSITION_STATES",
    "SCHEMA_VERSION",
    "build_active_result",
    "dec",
    "identity",
    "main",
    "render_summary",
    "ts",
    "validate_active_result",
    "validate_differential",
    "validate_native_metadata",
    "validate_result_set",
]

if __name__ == "__main__":
    main()
