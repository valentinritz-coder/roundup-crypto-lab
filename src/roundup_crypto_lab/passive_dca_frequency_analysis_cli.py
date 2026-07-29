"""Aggregate passive DCA frequency campaign artifacts into robust reports."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from roundup_crypto_lab.dca_registry import load_registry
from roundup_crypto_lab.passive_dca_frequency_analysis import (
    aggregate_frequency_results,
    write_frequency_analysis_outputs,
)
from roundup_crypto_lab.passive_dca_frequency_campaign import load_json


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("exploratory", "confirmation"),
        required=True,
    )
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    result_files = sorted(args.results_dir.rglob("frequency-scenario.json"))
    if not result_files:
        raise ValueError("passive frequency analysis found no scenario artifacts")
    payload = aggregate_frequency_results(
        campaign=load_json(args.campaign),
        registry=load_registry(args.registry),
        policy=load_json(args.policy),
        phase=args.phase,
        result_files=result_files,
        repository_commit=args.repository_commit,
    )
    write_frequency_analysis_outputs(payload, args.output_dir)


if __name__ == "__main__":
    main()
