import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from roundup_crypto_lab.kraken_ohlcv import ImportError, regenerate_manifest, write_feather
from roundup_crypto_lab.kraken_seed_window import apply_seed_start_date, parse_start_date


def candles(start: datetime, count: int):
    return [
        (
            start + timedelta(hours=4 * index),
            Decimal(1),
            Decimal(3),
            Decimal(1),
            Decimal(2),
            Decimal(4),
        )
        for index in range(count)
    ]


def prepared_dataset(tmp_path: Path, *, btc_start: datetime, eth_start: datetime) -> None:
    write_feather(candles(btc_start, 12), tmp_path, "BTC/EUR")
    write_feather(candles(eth_start, 12), tmp_path, "ETH/EUR")
    regenerate_manifest(
        tmp_path,
        source_metadata={
            "source_release_tag": "tag",
            "source_asset_name": "asset.zip",
            "source_archive_sha256": "abc",
        },
        repository_commit="commit",
        freqtrade_version="2026.6",
        freqtrade_commit="freqtrade-commit",
    )


def test_parse_start_date_is_strict() -> None:
    assert parse_start_date("20240401") == datetime(2024, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="strict YYYYMMDD"):
        parse_start_date("2024-04-01")
    with pytest.raises(ValueError, match="invalid calendar"):
        parse_start_date("20240230")


def test_seed_window_trims_both_pairs_and_updates_manifest(tmp_path: Path) -> None:
    start = datetime(2024, 3, 31, tzinfo=UTC)
    prepared_dataset(tmp_path, btc_start=start, eth_start=start)

    manifest = apply_seed_start_date(tmp_path, "20240401")

    assert manifest["requested_start_date"] == "20240401"
    assert manifest["requested_start_timestamp"] == "2024-04-01T00:00:00+00:00"
    for pair in ("BTC_EUR", "ETH_EUR"):
        frame = pd.read_feather(tmp_path / f"{pair}-4h.feather")
        first = pd.to_datetime(frame["date"], utc=True).iloc[0].to_pydatetime()
        assert first == datetime(2024, 4, 1, tzinfo=UTC)
    saved = json.loads((tmp_path / "kraken-ohlcv-manifest.json").read_text())
    assert saved["requested_start_date"] == "20240401"
    assert all(row["first_timestamp"].startswith("2024-04-01T00:00:00") for row in saved["datasets"])


def test_seed_window_rejects_pair_starting_after_request(tmp_path: Path) -> None:
    requested = datetime(2024, 4, 1, tzinfo=UTC)
    prepared_dataset(
        tmp_path,
        btc_start=requested,
        eth_start=requested + timedelta(hours=4),
    )
    with pytest.raises(ImportError, match="ETH/EUR.*after requested"):
        apply_seed_start_date(tmp_path, "20240401")


def test_seed_window_rejects_missing_requested_boundary(tmp_path: Path) -> None:
    start = datetime(2024, 3, 31, tzinfo=UTC)
    prepared_dataset(tmp_path, btc_start=start, eth_start=start)
    eth_path = tmp_path / "ETH_EUR-4h.feather"
    frame = pd.read_feather(eth_path)
    dates = pd.to_datetime(frame["date"], utc=True)
    frame = frame.loc[dates != datetime(2024, 4, 1, tzinfo=UTC)].reset_index(drop=True)
    frame.to_feather(eth_path, compression="lz4", compression_level=9)

    with pytest.raises(ImportError, match="no candle at requested boundary"):
        apply_seed_start_date(tmp_path, "20240401")


def test_seed_window_rejects_start_after_all_closed_candles(tmp_path: Path) -> None:
    start = datetime(2024, 3, 1, tzinfo=UTC)
    prepared_dataset(tmp_path, btc_start=start, eth_start=start)
    with pytest.raises(ImportError, match="no closed candles"):
        apply_seed_start_date(tmp_path, "20250101")
