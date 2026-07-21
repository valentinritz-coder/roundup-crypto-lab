"""Import Kraken's official 240-minute OHLCVT CSV exports into Freqtrade Feather data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED = {"XBTEUR_240.csv": "BTC/EUR", "ETHEUR_240.csv": "ETH/EUR"}
INTERVAL = timedelta(hours=4)
FREQTRADE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class ImportError(ValueError):
    """Raised when a source archive is not a safe, complete Kraken OHLCVT dataset."""


@dataclass(frozen=True)
class ImportedPair:
    pair: str
    candles: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal]]
    gaps: list[str]
    duplicates_removed: int
    filename: str
    sha256: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ImportError(f"unsafe ZIP member: {member.filename}")
        bundle.extractall(destination)


def source_files(source: Path) -> dict[str, Path]:
    """Return exactly one real file for each required basename, rejecting ambiguity."""
    root = source
    temp: tempfile.TemporaryDirectory[str] | None = None
    if source.is_file():
        if not zipfile.is_zipfile(source):
            raise ImportError("input must be a ZIP archive or directory")
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        _safe_extract(source, root)
    try:
        found: dict[str, list[Path]] = {name: [] for name in REQUIRED}
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file() or "__MACOSX" in relative.parts or path.name.startswith("._"):
                continue
            if path.name in found:
                found[path.name].append(path)
        result: dict[str, Path] = {}
        for name, choices in found.items():
            if not choices:
                raise ImportError(f"missing required Kraken file: {name}")
            if len(choices) != 1:
                raise ImportError(f"ambiguous required Kraken file: {name}")
            result[name] = choices[0]
        # A temporary extraction cannot outlive its returned paths.
        if temp:
            persistent = Path(tempfile.mkdtemp(prefix="kraken-ohlc-source-"))
            for name, path in result.items():
                shutil.copy2(path, persistent / name)
                result[name] = persistent / name
        return result
    finally:
        if temp:
            temp.cleanup()


def _timestamp(value: str) -> datetime:
    try:
        number = Decimal(value)
        if number != number.to_integral_value():
            raise ValueError
        return datetime.fromtimestamp(int(number), UTC)
    except (InvalidOperation, OverflowError, OSError, ValueError) as exc:
        raise ImportError(f"invalid UTC Unix timestamp: {value!r}") from exc


def _number(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ImportError(f"invalid {field}: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ImportError(f"{field} must be finite and non-negative")
    return result


def read_kraken_csv(path: Path, now: datetime | None = None) -> tuple[list, list[str], int]:
    now = now or datetime.now(UTC)
    rows: dict[datetime, tuple] = {}
    duplicates = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        first = next(reader, None)
        if first is None:
            raise ImportError(f"empty CSV: {path.name}")
        header = [item.strip().lower() for item in first]
        if header[0] in {"time", "timestamp"}:
            expected = ["open", "high", "low", "close", "vwap", "volume", "count"]
            if header[1:] != expected:
                raise ImportError(f"unexpected Kraken OHLCVT header in {path.name}")
            data_rows = reader
        else:
            data_rows = iter([first, *reader])
        for line, row in enumerate(data_rows, start=2 if header[0] in {"time", "timestamp"} else 1):
            if len(row) != 8:
                raise ImportError(f"malformed row {line} in {path.name}: expected 8 columns")
            timestamp = _timestamp(row[0])
            if timestamp.minute or timestamp.second or timestamp.microsecond or timestamp.hour % 4:
                raise ImportError(
                    f"timestamp is not aligned to a 4h UTC boundary: {timestamp.isoformat()}"
                )
            open_, high, low, close, volume = (
                _number(row[i], name)
                for i, name in ((1, "open"), (2, "high"), (3, "low"), (4, "close"), (6, "volume"))
            )
            candle = (timestamp, open_, high, low, close, volume)
            if high < max(open_, close, low) or low > min(open_, close, high):
                raise ImportError(f"invalid OHLC range at {timestamp.isoformat()}")
            if timestamp in rows:
                if rows[timestamp] != candle:
                    raise ImportError(f"conflicting duplicate timestamp: {timestamp.isoformat()}")
                duplicates += 1
            else:
                rows[timestamp] = candle
    closed_before = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=now.hour % 4)
    candles = [value for timestamp, value in sorted(rows.items()) if timestamp < closed_before]
    if not candles:
        raise ImportError(f"no closed candles remain in {path.name}")
    gaps = [
        f"{previous.isoformat()}..{current.isoformat()}"
        for previous, current in zip(
            (item[0] for item in candles), (item[0] for item in candles[1:]), strict=False
        )
        if current - previous > INTERVAL
    ]
    return candles, gaps, duplicates


def _filename(pair: str) -> str:
    # Verified against Freqtrade 2026.6 IDataHandler._pair_data_filename and FeatherDataHandler.
    return f"{pair.replace('/', '_')}-4h.feather"


def write_feather(candles: list, destination: Path, pair: str) -> Path:
    import pandas as pd

    destination.mkdir(parents=True, exist_ok=True)
    output = destination / _filename(pair)
    frame = pd.DataFrame(candles, columns=FREQTRADE_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.as_unit("ms")
    for column in FREQTRADE_COLUMNS[1:]:
        frame[column] = frame[column].map(float)
    frame.to_feather(output, compression="lz4", compression_level=9)
    return output


def import_dataset(
    source: Path,
    destination: Path,
    *,
    release_tag: str,
    asset_name: str,
    archive_sha256: str,
    repository_commit: str,
    freqtrade_version: str,
    freqtrade_commit: str,
    now: datetime | None = None,
) -> dict:
    entries = []
    sources = source_files(source)
    for basename, pair in REQUIRED.items():
        candles, gaps, duplicates = read_kraken_csv(sources[basename], now)
        output = write_feather(candles, destination, pair)
        entries.append(
            {
                "pair": pair,
                "timeframe": "4h",
                "number_of_candles": len(candles),
                "first_timestamp": candles[0][0].isoformat(),
                "last_timestamp": candles[-1][0].isoformat(),
                "missing_intervals": gaps,
                "duplicate_rows_removed": duplicates,
                "output_filename": output.name,
                "output_file_sha256": sha256(output),
            }
        )
    manifest = {
        "source_release_tag": release_tag,
        "source_asset_name": asset_name,
        "source_archive_sha256": archive_sha256,
        "repository_commit": repository_commit,
        "freqtrade_version": freqtrade_version,
        "freqtrade_commit": freqtrade_commit,
        "generation_timestamp": datetime.now(UTC).isoformat(),
        "datasets": entries,
    }
    (destination / "kraken-ohlcv-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--datadir", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--asset-name", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--freqtrade-version", default="2026.6")
    parser.add_argument("--freqtrade-commit", required=True)
    args = parser.parse_args()
    import_dataset(
        args.source,
        args.datadir,
        release_tag=args.release_tag,
        asset_name=args.asset_name,
        archive_sha256=args.archive_sha256,
        repository_commit=args.repository_commit,
        freqtrade_version=args.freqtrade_version,
        freqtrade_commit=args.freqtrade_commit,
    )


if __name__ == "__main__":
    main()


def dataset_entries(destination: Path) -> list[dict]:
    """Read and strictly validate the two supported Freqtrade spot Feather files."""
    import pandas as pd

    entries = []
    for pair in REQUIRED.values():
        output = destination / _filename(pair)
        if not output.is_file():
            raise ImportError(f"missing Feather data for {pair}")
        frame = pd.read_feather(output)
        if list(frame.columns) != FREQTRADE_COLUMNS or frame.empty:
            raise ImportError(f"invalid Freqtrade Feather schema for {pair}")
        dates = pd.to_datetime(frame["date"], utc=True)
        if not dates.is_monotonic_increasing or dates.duplicated().any():
            raise ImportError(f"unsorted or duplicate timestamps in {pair}")
        candles = list(zip(dates.dt.to_pydatetime(), strict=False))
        gaps = [
            f"{left.isoformat()}..{right.isoformat()}"
            for (left,), (right,) in zip(candles, candles[1:], strict=False)
            if right - left > INTERVAL
        ]
        entries.append(
            {
                "pair": pair,
                "timeframe": "4h",
                "number_of_candles": len(frame),
                "first_timestamp": dates.iloc[0].isoformat(),
                "last_timestamp": dates.iloc[-1].isoformat(),
                "missing_intervals": gaps,
                "duplicate_rows_removed": 0,
                "output_filename": output.name,
                "output_file_sha256": sha256(output),
            }
        )
    return entries


def regenerate_manifest(
    destination: Path,
    *,
    source_metadata: dict,
    repository_commit: str,
    freqtrade_version: str,
    freqtrade_commit: str,
) -> dict:
    """Regenerate cache metadata after a merge while preserving its immutable source identity."""
    manifest = {
        key: source_metadata[key]
        for key in ("source_release_tag", "source_asset_name", "source_archive_sha256")
    }
    manifest.update(
        {
            "repository_commit": repository_commit,
            "freqtrade_version": freqtrade_version,
            "freqtrade_commit": freqtrade_commit,
            "generation_timestamp": datetime.now(UTC).isoformat(),
            "update_timestamp": datetime.now(UTC).isoformat(),
            "datasets": dataset_entries(destination),
        }
    )
    (destination / "kraken-ohlcv-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def load_and_verify_manifest(destination: Path) -> dict:
    manifest_path = destination / "kraken-ohlcv-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportError("missing or invalid dataset manifest") from exc
    actual = dataset_entries(destination)

    # Duplicate-removal provenance exists only at import time; all persistent fields must match.
    def comparable(entries: list[dict]) -> list[dict]:
        return [
            {key: value for key, value in entry.items() if key != "duplicate_rows_removed"}
            for entry in entries
        ]

    if comparable(manifest.get("datasets", [])) != comparable(actual):
        raise ImportError("dataset manifest is stale or does not match Feather data")
    return manifest


def historical_timerange(destination: Path) -> str:
    """Validate contiguous common history without requiring the archive to be current."""
    entries = load_and_verify_manifest(destination)["datasets"]
    start = max(datetime.fromisoformat(item["first_timestamp"]) for item in entries)
    end = min(datetime.fromisoformat(item["last_timestamp"]) for item in entries)
    required_start = end - INTERVAL * 480 - timedelta(days=180)
    if required_start < start:
        raise ImportError("insufficient common history after 480-candle warm-up")
    for entry in entries:
        for gap in entry["missing_intervals"]:
            gap_start, gap_end = (datetime.fromisoformat(value) for value in gap.split(".."))
            if gap_end > required_start and gap_start < end:
                raise ImportError("missing 4h interval intersects required validation history")
    return f"{(end - timedelta(days=180)).strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"


def common_timerange(destination: Path, now: datetime | None = None) -> str:
    """Validate historical coverage and require a recent fully closed common candle."""
    timerange = historical_timerange(destination)
    end = min(
        datetime.fromisoformat(item["last_timestamp"])
        for item in load_and_verify_manifest(destination)["datasets"]
    )
    latest_closed = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    latest_closed -= timedelta(hours=latest_closed.hour % 4)
    if end < latest_closed - INTERVAL:
        raise ImportError("common dataset end is not a recent closed 4h candle")
    return timerange


def update_request(destination: Path, now: datetime | None = None) -> str:
    """Return the supported Freqtrade download option for weekly update or initial catch-up."""
    now = now or datetime.now(UTC)
    closed = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=now.hour % 4)
    end = min(
        datetime.fromisoformat(item["last_timestamp"])
        for item in load_and_verify_manifest(destination)["datasets"]
    )
    if closed - end <= timedelta(days=8):
        return "--days 8"
    # Freqtrade 2026.6 download-data supports --timerange (mutually exclusive with --days).
    return f"--timerange {int((end - timedelta(days=1)).timestamp())}-"
