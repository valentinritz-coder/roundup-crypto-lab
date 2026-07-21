import json
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.kraken_ohlcv import ImportError, read_kraken_csv, source_files


def row(timestamp: int, close: str = "2") -> str:
    return f"{timestamp},1,3,1,{close},2,4,1\n"


def test_exact_files_ignore_apple_and_confusing_names(tmp_path: Path) -> None:
    for name in (
        "XBTEUR_240.csv",
        "ETHEUR_240.csv",
        "XBTEURC_240.csv",
        "XBTEUROP_240.csv",
        "ETHEURC_240.csv",
        "ETHEUROP_240.csv",
        "ETHWEUR_240.csv",
        "._XBTEUR_240.csv",
        "._ETHEUR_240.csv",
    ):
        target = tmp_path / ("__MACOSX" if name.startswith("._") else "") / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(row(0))
    assert set(source_files(tmp_path)) == {"XBTEUR_240.csv", "ETHEUR_240.csv"}


def test_missing_or_ambiguous_real_files_rejected(tmp_path: Path) -> None:
    (tmp_path / "XBTEUR_240.csv").write_text(row(0))
    with pytest.raises(ImportError, match="missing"):
        source_files(tmp_path)
    (tmp_path / "ETHEUR_240.csv").write_text(row(0))
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "XBTEUR_240.csv").write_text(row(0))
    with pytest.raises(ImportError, match="ambiguous"):
        source_files(tmp_path)


def test_zip_path_traversal_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../XBTEUR_240.csv", row(0))
    with pytest.raises(ImportError, match="unsafe"):
        source_files(archive)


def test_csv_utc_alignment_duplicates_gaps_and_open_candle(tmp_path: Path) -> None:
    csv = tmp_path / "XBTEUR_240.csv"
    csv.write_text("time,open,high,low,close,vwap,volume,count\n" + row(0) + row(0) + row(8 * 3600))
    candles, gaps, duplicates = read_kraken_csv(csv, datetime(1970, 1, 1, 13, tzinfo=UTC))
    assert [x[0] for x in candles] == [
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1970, 1, 1, 8, tzinfo=UTC),
    ]
    assert duplicates == 1 and len(gaps) == 1


def test_conflicting_duplicate_and_bad_alignment_rejected(tmp_path: Path) -> None:
    csv = tmp_path / "x.csv"
    csv.write_text(row(0) + "0,1,3,1,2,2,5,1\n")
    with pytest.raises(ImportError, match="conflicting"):
        read_kraken_csv(csv, datetime(1970, 1, 2, tzinfo=UTC))
    csv.write_text(row(60))
    with pytest.raises(ImportError, match="aligned"):
        read_kraken_csv(csv, datetime(1970, 1, 2, tzinfo=UTC))


def test_manifest_regeneration_and_common_overlap(tmp_path: Path) -> None:
    from roundup_crypto_lab.kraken_ohlcv import (
        common_timerange,
        load_and_verify_manifest,
        regenerate_manifest,
        write_feather,
    )
    from roundup_crypto_lab.merge_kraken_data import merge

    base = datetime(2025, 1, 1, tzinfo=UTC)

    def candles(start: int, count: int, price: int = 1):
        return [
            (
                base + timedelta(hours=4 * (start + n)),
                Decimal(price),
                Decimal(price + 2),
                Decimal(price),
                Decimal(price + 1),
                Decimal(4),
            )
            for n in range(count)
        ]

    write_feather(candles(0, 1600), tmp_path, "BTC/EUR")
    write_feather(candles(4, 1598), tmp_path, "ETH/EUR")
    source = {
        "source_release_tag": "tag",
        "source_asset_name": "asset",
        "source_archive_sha256": "abc",
    }
    manifest = regenerate_manifest(
        tmp_path,
        source_metadata=source,
        repository_commit="seed",
        freqtrade_version="2026.6",
        freqtrade_commit="commit",
    )
    assert common_timerange(tmp_path).endswith(
        (base + timedelta(hours=4 * 1601)).strftime("%Y%m%d")
    )
    old_checksum = manifest["datasets"][0]["output_file_sha256"]
    recent = tmp_path / "recent"
    recent.mkdir()
    write_feather(candles(1599, 3, 9), recent, "BTC/EUR")
    write_feather(candles(1602, 2, 9), recent, "ETH/EUR")
    merge(tmp_path, recent, now=base + timedelta(hours=4 * 1700))
    updated = load_and_verify_manifest(tmp_path)
    assert updated["datasets"][0]["output_file_sha256"] != old_checksum
    stale = updated.copy()
    stale["datasets"] = manifest["datasets"]
    (tmp_path / "kraken-ohlcv-manifest.json").write_text(json.dumps(stale))
    with pytest.raises(ImportError, match="stale"):
        load_and_verify_manifest(tmp_path)
