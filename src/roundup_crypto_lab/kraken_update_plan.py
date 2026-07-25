"""Plan fast weekly Kraken OHLCV maintenance without trade-history backfills."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from roundup_crypto_lab.kraken_ohlcv import (
    ImportError,
    gap_diagnostics,
    load_and_verify_manifest,
)

RECENT_OVERLAP_DAYS = 8


@dataclass(frozen=True)
class UpdatePlan:
    mode: Literal["recent_overlap", "historical_rebuild_required"]
    days: int | None
    reason: str
    first_problem_timestamp: str | None = None


def build_update_plan(destination: Path, now: datetime | None = None) -> UpdatePlan:
    """Return a bounded OHLCV refresh or require an explicit historical rebuild."""
    now = now or datetime.now(UTC)
    closed = now.replace(minute=0, second=0, microsecond=0)
    closed -= timedelta(hours=closed.hour % 4)

    try:
        manifest = load_and_verify_manifest(destination)
        diagnostics = gap_diagnostics(destination)
    except ImportError as error:
        return UpdatePlan(
            mode="historical_rebuild_required",
            days=None,
            reason=f"prepared dataset is not eligible for weekly maintenance: {error}",
        )

    relevant = [gap for gap in diagnostics if gap["intersects_validation"]]
    if relevant:
        first = min(gap["start"] for gap in relevant)
        return UpdatePlan(
            mode="historical_rebuild_required",
            days=None,
            reason="required historical coverage contains missing 4h candles",
            first_problem_timestamp=first.isoformat(),
        )

    end = min(
        datetime.fromisoformat(entry["last_timestamp"])
        for entry in manifest["datasets"]
    )
    if closed - end > timedelta(days=RECENT_OVERLAP_DAYS):
        return UpdatePlan(
            mode="historical_rebuild_required",
            days=None,
            reason=(
                "prepared dataset is too stale for the bounded weekly OHLCV overlap; "
                "run Seed Kraken data"
            ),
            first_problem_timestamp=end.isoformat(),
        )

    return UpdatePlan(
        mode="recent_overlap",
        days=RECENT_OVERLAP_DAYS,
        reason="normal bounded weekly OHLCV overlap",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datadir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    plan = build_update_plan(args.datadir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(asdict(plan), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(asdict(plan), sort_keys=True))
    if plan.mode == "historical_rebuild_required":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
