"""Run one-shot diagnostics without discarding the comparison on known divergences."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from roundup_crypto_lab import one_shot_diagnostic as diagnostic
from roundup_crypto_lab import one_shot_differential as differential

_CUSTOM_EXIT_REASONS = (
    "control_breakdown_10",
    "momentum_lost_or_below_sma20",
    "rsi2_or_sma20_mean_reversion",
    "distance_reverted_to_ema20",
)


def register_native_exit_reasons() -> None:
    """Normalize every vectorized exit tag introduced by research batch one."""
    differential._NATIVE_REASON_MAP.update(
        {reason: "exit_signal" for reason in _CUSTOM_EXIT_REASONS}
    )


def main() -> None:
    register_native_exit_reasons()
    if len(sys.argv) > 1 and sys.argv[1] == "combine":
        parser = argparse.ArgumentParser()
        parser.add_argument("command", choices=["combine"])
        parser.add_argument("--result", action="append", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        diagnostic._write(args.output, diagnostic.combine(args.result))
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-zip", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diagnostic._write(
        args.output,
        diagnostic.diagnose(args.native_zip, args.active, args.strategy),
    )


if __name__ == "__main__":
    main()
