"""Run one campaign scenario and record expected data-quality exclusions."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from roundup_crypto_lab import dca_controlled_comparison as controlled_comparison
from roundup_crypto_lab.dca_baselines import DEFAULT_STRATEGY_REGISTRY
from roundup_crypto_lab.dca_decimal_safety import canonical_decimal, consume_fifo

run_comparison = controlled_comparison.run_comparison
write_outputs = controlled_comparison.write_outputs

EXCLUSION_SCHEMA_VERSION = "dca-campaign-scenario-exclusion/v1"
EXCLUDABLE_DATA_ERRORS = (
    "missing Kraken data for ",
    "invalid OHLCV columns for ",
    "timestamps must be monotonic and unique for ",
    "OHLC values must be finite and positive for ",
    "volume must be finite and non-negative for ",
    "insufficient Kraken coverage at timerange start for ",
    "insufficient Kraken coverage at timerange end for ",
    "critical 4h candle gap in ",
)


def is_excludable_data_error(error: ValueError) -> bool:
    """Return whether a comparison failure is an expected input-data exclusion."""
    return str(error).startswith(EXCLUDABLE_DATA_ERRORS)


def exclusion_payload(
    *,
    pair: str,
    timeframe: str,
    timerange: str,
    window_set_id: str,
    phase: str,
    variant_id: str,
    reason: str,
) -> dict[str, Any]:
    """Build the machine-readable record emitted for an excluded scenario."""
    return {
        "schema_version": EXCLUSION_SCHEMA_VERSION,
        "status": "excluded",
        "scenario": {
            "pair": pair,
            "timeframe": timeframe,
            "timerange": timerange,
            "window_set_id": window_set_id,
            "phase": phase,
            "variant_id": variant_id,
        },
        "exclusion": {
            "category": "input-data-quality",
            "reason": reason,
        },
    }


def _install_decimal_safety() -> None:
    """Keep campaign execution strict while normalizing sub-quantum Decimal residue."""
    controlled_comparison._canonical = canonical_decimal
    controlled_comparison._consume_fifo = consume_fifo


def run_campaign_scenario(
    *,
    data_dir: Path,
    pair: str,
    timeframe: str,
    timerange: str,
    registry_path: Path,
    initial_capital: str,
    monthly_budget: str,
    contribution_day: int,
    fee: str,
    repository_commit: str,
    output_dir: Path,
    window_set_id: str,
    phase: str,
    variant_id: str,
) -> str:
    """Run a comparison, or persist an explicit exclusion without failing the campaign."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _install_decimal_safety()
    try:
        payload = run_comparison(
            data_dir=data_dir,
            pair=pair,
            timeframe=timeframe,
            timerange=timerange,
            registry_path=registry_path,
            initial_capital=initial_capital,
            monthly_budget=monthly_budget,
            contribution_day=contribution_day,
            fee=fee,
            repository_commit=repository_commit,
        )
    except ValueError as error:
        if not is_excludable_data_error(error):
            raise
        excluded = exclusion_payload(
            pair=pair,
            timeframe=timeframe,
            timerange=timerange,
            window_set_id=window_set_id,
            phase=phase,
            variant_id=variant_id,
            reason=str(error),
        )
        (output_dir / "scenario-exclusion.json").write_text(
            json.dumps(excluded, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return "excluded"

    payload["campaign"] = {
        "window_set_id": window_set_id,
        "phase": phase,
        "variant_id": variant_id,
    }
    write_outputs(
        payload,
        output_dir,
        registry_path=registry_path,
        repository_commit=repository_commit,
    )
    return "completed"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("user_data/data/kraken"))
    parser.add_argument("--pair", required=True, choices=("BTC/EUR", "ETH/EUR"))
    parser.add_argument("--timeframe", required=True, choices=("4h",))
    parser.add_argument("--timerange", required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_STRATEGY_REGISTRY)
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--fee", required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--window-set-id", required=True)
    parser.add_argument("--phase", required=True, choices=("exploratory", "confirmation"))
    parser.add_argument("--variant-id", required=True)
    args = parser.parse_args(argv)
    status = run_campaign_scenario(
        data_dir=args.data_dir,
        pair=args.pair,
        timeframe=args.timeframe,
        timerange=args.timerange,
        registry_path=args.registry,
        initial_capital=args.initial_capital,
        monthly_budget=args.monthly_budget,
        contribution_day=args.contribution_day,
        fee=args.fee,
        repository_commit=args.repository_commit,
        output_dir=args.output_dir,
        window_set_id=args.window_set_id,
        phase=args.phase,
        variant_id=args.variant_id,
    )
    print(status)


if __name__ == "__main__":
    main()
