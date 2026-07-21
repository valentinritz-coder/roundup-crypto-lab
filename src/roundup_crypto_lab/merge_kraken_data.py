"""Safely merge recent reconstructed Kraken candles into a seeded data cache."""

from __future__ import annotations

import argparse
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from .kraken_ohlcv import (
    FREQTRADE_COLUMNS,
    REQUIRED,
    ImportError,
    load_and_verify_manifest,
    regenerate_manifest,
)


def merge(
    seeded: Path,
    recent_root: Path,
    *,
    now: datetime | None = None,
    repository_commit: str = "unknown",
    freqtrade_version: str | None = None,
    freqtrade_commit: str | None = None,
) -> dict:
    source = load_and_verify_manifest(seeded)
    now = now or datetime.now(UTC)
    cutoff = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=now.hour % 4)
    for pair in REQUIRED.values():
        filename = f"{pair.replace('/', '_')}-4h.feather"
        old_path = seeded / filename
        candidates = list(recent_root.rglob(filename))
        if len(candidates) != 1:
            raise ImportError(f"missing or ambiguous recent Feather data for {pair}")
        old, recent = pd.read_feather(old_path), pd.read_feather(candidates[0])
        for frame, label in ((old, "seeded"), (recent, "recent")):
            if list(frame.columns) != FREQTRADE_COLUMNS:
                raise ImportError(f"invalid {label} Feather schema")
            frame["date"] = pd.to_datetime(frame["date"], utc=True)
            if (
                frame.empty
                or frame.date.duplicated().any()
                or not frame.date.is_monotonic_increasing
            ):
                raise ImportError(f"invalid {label} timestamps")
            if any(frame.date.dt.minute) or any(frame.date.dt.hour % 4):
                raise ImportError(f"unaligned {label} timestamps")
            values = frame[FREQTRADE_COLUMNS[1:]]
            if not values.apply(
                lambda column: pd.to_numeric(column, errors="coerce").notna().all()
            ).all():
                raise ImportError(f"non-numeric {label} candles")
            if not values.apply(lambda column: column.map(float).map(math.isfinite).all()).all():
                raise ImportError(f"non-finite {label} candles")
            if (
                (frame["volume"] < 0).any()
                or (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
                or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()
            ):
                raise ImportError(f"invalid {label} OHLCV values")
        merged = pd.concat([old, recent]).sort_values("date").drop_duplicates("date", keep="last")
        merged = merged[merged.date < cutoff].reset_index(drop=True)
        if not set(old.date).issubset(set(merged.date)) or merged.date.iloc[0] != old.date.iloc[0]:
            raise ImportError("merge would delete historical candles")
        merged.to_feather(old_path, compression="lz4", compression_level=9)
    return regenerate_manifest(
        seeded,
        source_metadata=source,
        repository_commit=repository_commit,
        freqtrade_version=freqtrade_version or source["freqtrade_version"],
        freqtrade_commit=freqtrade_commit or source["freqtrade_commit"],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seeded", type=Path)
    parser.add_argument("recent", type=Path)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--freqtrade-version", required=True)
    parser.add_argument("--freqtrade-commit", required=True)
    args = parser.parse_args()
    merge(
        args.seeded,
        args.recent,
        repository_commit=args.repository_commit,
        freqtrade_version=args.freqtrade_version,
        freqtrade_commit=args.freqtrade_commit,
    )


if __name__ == "__main__":
    main()
