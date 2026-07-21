import zipfile
from datetime import UTC, datetime
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
