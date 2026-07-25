"""Repair shared one-candle gaps in imported Kraken 4h OHLCV seed data."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from roundup_crypto_lab.kraken_ohlcv import FREQTRADE_COLUMNS, REQUIRED, _filename, sha256

INTERVAL = timedelta(hours=4)


def _single_candle_gaps(frame: pd.DataFrame) -> set[pd.Timestamp]:
    dates = pd.to_datetime(frame["date"], utc=True)
    return {
        left + INTERVAL
        for left, right in zip(dates, dates.iloc[1:], strict=False)
        if right - left == 2 * INTERVAL
    }


def repair_shared_single_candle_gaps(datadir: Path) -> list[dict]:
    """Fill gaps shared by every supported pair using the previous close and zero volume."""
    frames: dict[str, pd.DataFrame] = {}
    gaps_by_pair: dict[str, set[pd.Timestamp]] = {}
    for pair in REQUIRED.values():
        path = datadir / _filename(pair)
        frame = pd.read_feather(path)
        if list(frame.columns) != FREQTRADE_COLUMNS or frame.empty:
            raise ValueError(f"invalid Freqtrade Feather data for {pair}")
        frame["date"] = pd.to_datetime(frame["date"], utc=True)
        frame = frame.sort_values("date").reset_index(drop=True)
        frames[pair] = frame
        gaps_by_pair[pair] = _single_candle_gaps(frame)

    shared = set.intersection(*gaps_by_pair.values()) if gaps_by_pair else set()
    synthetic: list[dict] = []
    for pair, frame in frames.items():
        additions = []
        for timestamp in sorted(shared):
            previous = frame.loc[frame["date"] < timestamp].iloc[-1]
            close = float(previous["close"])
            additions.append(
                {
                    "date": timestamp,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 0.0,
                }
            )
            synthetic.append(
                {
                    "pair": pair,
                    "timestamp": timestamp.isoformat(),
                    "method": "previous_close_zero_volume",
                    "source": "shared_single_candle_gap",
                }
            )
        if additions:
            repaired = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
            repaired = repaired.sort_values("date").reset_index(drop=True)
            repaired["date"] = pd.to_datetime(repaired["date"], utc=True).dt.as_unit("ms")
            repaired.to_feather(
                datadir / _filename(pair), compression="lz4", compression_level=9
            )

    manifest_path = datadir / "kraken-ohlcv-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["synthetic_candles"] = synthetic
    for dataset in manifest["datasets"]:
        pair = dataset["pair"]
        frame = pd.read_feather(datadir / _filename(pair))
        dates = pd.to_datetime(frame["date"], utc=True)
        dataset["number_of_candles"] = len(frame)
        dataset["first_timestamp"] = dates.iloc[0].isoformat()
        dataset["last_timestamp"] = dates.iloc[-1].isoformat()
        dataset["missing_intervals"] = [
            f"{left.isoformat()}..{right.isoformat()}"
            for left, right in zip(dates, dates.iloc[1:], strict=False)
            if right - left > INTERVAL
        ]
        dataset["synthetic_candles"] = [
            item for item in synthetic if item["pair"] == pair
        ]
        dataset["output_file_sha256"] = sha256(datadir / _filename(pair))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return synthetic


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datadir", type=Path)
    args = parser.parse_args()
    repaired = repair_shared_single_candle_gaps(args.datadir)
    for item in repaired:
        print(
            f"pair={item['pair']} timestamp={item['timestamp']} "
            f"method={item['method']}"
        )


if __name__ == "__main__":
    main()
