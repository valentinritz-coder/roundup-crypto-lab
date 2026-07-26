import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from roundup_crypto_lab.kraken_ohlcv import write_feather
from roundup_crypto_lab.repair_kraken_seed import repair_shared_single_candle_gaps


def _candles(base: datetime) -> list[tuple]:
    return [
        (
            base + timedelta(hours=4 * index),
            Decimal(index + 1),
            Decimal(index + 2),
            Decimal(index),
            Decimal(index + 1),
            Decimal(5),
        )
        for index in range(5)
    ]


def _manifest(datadir: Path) -> None:
    datasets = []
    for pair in ("BTC/EUR", "ETH/EUR"):
        path = datadir / f"{pair.replace('/', '_')}-4h.feather"
        frame = pd.read_feather(path)
        dates = pd.to_datetime(frame["date"], utc=True)
        datasets.append(
            {
                "pair": pair,
                "timeframe": "4h",
                "number_of_candles": len(frame),
                "first_timestamp": dates.iloc[0].isoformat(),
                "last_timestamp": dates.iloc[-1].isoformat(),
                "missing_intervals": [],
                "duplicate_rows_removed": 0,
                "output_filename": path.name,
                "output_file_sha256": "old",
            }
        )
    (datadir / "kraken-ohlcv-manifest.json").write_text(
        json.dumps(
            {
                "source_release_tag": "tag",
                "source_asset_name": "asset",
                "source_archive_sha256": "sha",
                "repository_commit": "commit",
                "freqtrade_version": "2026.6",
                "freqtrade_commit": "freqtrade",
                "generation_timestamp": datetime.now(UTC).isoformat(),
                "datasets": datasets,
            }
        )
    )


def test_repairs_shared_single_candle_gaps(tmp_path: Path) -> None:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    candles = _candles(base)
    for pair in ("BTC/EUR", "ETH/EUR"):
        write_feather(candles[:2] + candles[3:], tmp_path, pair)
    _manifest(tmp_path)

    repaired = repair_shared_single_candle_gaps(tmp_path)

    assert len(repaired) == 2
    assert {item["timestamp"] for item in repaired} == {candles[2][0].isoformat()}
    for pair in ("BTC/EUR", "ETH/EUR"):
        frame = pd.read_feather(tmp_path / f"{pair.replace('/', '_')}-4h.feather")
        row = frame.loc[pd.to_datetime(frame["date"], utc=True) == candles[2][0]].iloc[0]
        assert row["open"] == candles[1][4]
        assert row["high"] == candles[1][4]
        assert row["low"] == candles[1][4]
        assert row["close"] == candles[1][4]
        assert row["volume"] == 0

    manifest = json.loads((tmp_path / "kraken-ohlcv-manifest.json").read_text())
    assert len(manifest["synthetic_candles"]) == 2
    assert all(not item["missing_intervals"] for item in manifest["datasets"])


def test_repairs_pair_specific_single_candle_gap(tmp_path: Path) -> None:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    candles = _candles(base)
    write_feather(candles[:2] + candles[3:], tmp_path, "BTC/EUR")
    write_feather(candles, tmp_path, "ETH/EUR")
    _manifest(tmp_path)

    repaired = repair_shared_single_candle_gaps(tmp_path)

    assert repaired == [
        {
            "pair": "BTC/EUR",
            "timestamp": candles[2][0].isoformat(),
            "method": "previous_close_zero_volume",
            "source": "pair_specific_single_candle_gap",
        }
    ]
    btc = pd.read_feather(tmp_path / "BTC_EUR-4h.feather")
    btc["date"] = pd.to_datetime(btc["date"], utc=True)
    row = btc.loc[btc["date"] == candles[2][0]].iloc[0]
    assert row["open"] == candles[1][4]
    assert row["high"] == candles[1][4]
    assert row["low"] == candles[1][4]
    assert row["close"] == candles[1][4]
    assert row["volume"] == 0

    eth = pd.read_feather(tmp_path / "ETH_EUR-4h.feather")
    assert len(eth) == len(candles)

    manifest = json.loads((tmp_path / "kraken-ohlcv-manifest.json").read_text())
    btc_manifest = next(item for item in manifest["datasets"] if item["pair"] == "BTC/EUR")
    eth_manifest = next(item for item in manifest["datasets"] if item["pair"] == "ETH/EUR")
    assert btc_manifest["synthetic_candles"] == repaired
    assert eth_manifest["synthetic_candles"] == []
    assert btc_manifest["missing_intervals"] == []


def test_does_not_repair_multi_candle_gap(tmp_path: Path) -> None:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    candles = _candles(base)
    write_feather(candles[:1] + candles[3:], tmp_path, "BTC/EUR")
    write_feather(candles, tmp_path, "ETH/EUR")
    _manifest(tmp_path)

    assert repair_shared_single_candle_gaps(tmp_path) == []
    btc = pd.read_feather(tmp_path / "BTC_EUR-4h.feather")
    assert len(btc) == 3

    manifest = json.loads((tmp_path / "kraken-ohlcv-manifest.json").read_text())
    btc_manifest = next(item for item in manifest["datasets"] if item["pair"] == "BTC/EUR")
    assert btc_manifest["missing_intervals"]
