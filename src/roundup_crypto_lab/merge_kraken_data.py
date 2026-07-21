"""Merge reconstructed closed Kraken candles without discarding seeded history."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from .kraken_ohlcv import REQUIRED


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seeded", type=Path)
    parser.add_argument("recent", type=Path)
    args = parser.parse_args()
    cutoff = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=datetime.now(UTC).hour % 4
    )
    for pair in REQUIRED.values():
        name = f"{pair.replace('/', '_')}-4h.feather"
        old = args.seeded / name
        candidates = list(args.recent.rglob(name))
        if not old.exists() or len(candidates) != 1:
            raise SystemExit(f"missing Feather data for {pair}")
        before = pd.read_feather(old)
        recent = pd.read_feather(candidates[0])
        merged = (
            pd.concat([before, recent]).sort_values("date").drop_duplicates("date", keep="last")
        )
        merged = merged[pd.to_datetime(merged.date, utc=True) < cutoff].reset_index(drop=True)
        if len(merged) < len(before) or merged.date.iloc[0] != before.date.iloc[0]:
            raise SystemExit("historical candle disappeared")
        merged.to_feather(old, compression="lz4", compression_level=9)


if __name__ == "__main__":
    main()
