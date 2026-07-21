"""Command-line report for roundup CSV files."""

from __future__ import annotations

import argparse
from pathlib import Path

from .roundups import load_roundups


def _euros(cents: int) -> str:
    return f"{cents / 100:.2f} EUR"


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate exact-cent payment roundups.")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument(
        "--monthly-fixed-eur",
        type=int,
        default=40,
        help="Fixed monthly contribution in whole euros (default: 40).",
    )
    args = parser.parse_args()

    records = load_roundups(args.csv_file)
    roundup_total = sum(record.roundup_cents for record in records)
    fixed_total = args.monthly_fixed_eur * 100

    print(f"Transactions audited : {len(records)}")
    print(f"Roundups             : {_euros(roundup_total)}")
    print(f"Fixed contribution   : {_euros(fixed_total)}")
    print(f"Total contribution   : {_euros(roundup_total + fixed_total)}")
