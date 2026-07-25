"""Trim an imported Kraken release dataset to an exact common UTC start boundary."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from roundup_crypto_lab.kraken_ohlcv import (
    FREQTRADE_COLUMNS,
    REQUIRED,
    ImportError,
    dataset_entries,
)

START_DATE_PATTERN = re.compile(r"^\d{8}$")


def parse_start_date(value: str) -> datetime:
    """Parse a strict YYYYMMDD UTC midnight boundary."""

    if START_DATE_PATTERN.fullmatch(value) is None:
        raise ValueError("start date must use strict YYYYMMDD format")
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("start date contains an invalid calendar date") from error


def apply_seed_start_date(destination: Path, start_date: str) -> dict[str, object]:
    """Trim both supported pairs and regenerate manifest checksums.

    The requested candle must exist for every pair. This prevents a successful seed whose
    apparent configuration predates its actual common market coverage.
    """

    import pandas as pd

    boundary = parse_start_date(start_date)
    manifest_path = destination / "kraken-ohlcv-manifest.json"
    if not manifest_path.is_file():
        raise ImportError("missing imported Kraken manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for pair in REQUIRED.values():
        filename = f"{pair.replace('/', '_')}-4h.feather"
        path = destination / filename
        if not path.is_file():
            raise ImportError(f"missing Feather data for {pair}")
        frame = pd.read_feather(path)
        if list(frame.columns) != FREQTRADE_COLUMNS or frame.empty:
            raise ImportError(f"invalid Freqtrade Feather schema for {pair}")
        dates = pd.to_datetime(frame["date"], utc=True)
        source_start = dates.iloc[0].to_pydatetime()
        source_end = dates.iloc[-1].to_pydatetime()
        if source_start > boundary:
            raise ImportError(
                f"release history for {pair} starts at {source_start.isoformat()}, "
                f"after requested {boundary.isoformat()}"
            )
        filtered = frame.loc[dates >= boundary].reset_index(drop=True)
        if filtered.empty:
            raise ImportError(
                f"no closed candles for {pair} remain from {boundary.isoformat()} "
                f"through {source_end.isoformat()}"
            )
        first = pd.to_datetime(filtered["date"], utc=True).iloc[0].to_pydatetime()
        if first != boundary:
            raise ImportError(
                f"release history for {pair} has no candle at requested boundary "
                f"{boundary.isoformat()}; first retained candle is {first.isoformat()}"
            )
        filtered.to_feather(path, compression="lz4", compression_level=9)

    manifest["requested_start_date"] = start_date
    manifest["requested_start_timestamp"] = boundary.isoformat()
    manifest["generation_timestamp"] = datetime.now(UTC).isoformat()
    manifest["datasets"] = dataset_entries(destination)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datadir", type=Path)
    parser.add_argument("--start-date", required=True)
    args = parser.parse_args()
    apply_seed_start_date(args.datadir, args.start_date)


if __name__ == "__main__":
    main()
