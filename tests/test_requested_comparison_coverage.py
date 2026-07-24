from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.breakout_comparison import validate_prepared_data
from roundup_crypto_lab.kraken_ohlcv import (
    ImportError as KrakenImportError,
    common_timerange,
    regenerate_manifest,
    write_feather,
)


def _candles(last: datetime, count: int) -> list[tuple]:
    first = last - timedelta(hours=4 * (count - 1))
    return [
        (
            first + timedelta(hours=4 * index),
            Decimal("100"),
            Decimal("102"),
            Decimal("99"),
            Decimal("101"),
            Decimal("4"),
        )
        for index in range(count)
    ]


def test_comparison_accepts_cache_missing_only_an_unused_latest_candle(tmp_path: Path) -> None:
    # The requested timerange ends at 2026-07-23 00:00 UTC and therefore reads through the
    # 2026-07-22 20:00 candle.  The next fully closed 4h candle may be absent during development.
    last_required = datetime(2026, 7, 22, 20, tzinfo=UTC)
    candles = _candles(last_required, 1600)
    for pair in ("BTC/EUR", "ETH/EUR"):
        write_feather(candles, tmp_path, pair)
    regenerate_manifest(
        tmp_path,
        source_metadata={
            "source_release_tag": "test",
            "source_asset_name": "test.zip",
            "source_archive_sha256": "abc",
        },
        repository_commit="test",
        freqtrade_version="2026.6",
        freqtrade_commit="test",
    )

    # The general cache-freshness contract still notices that the 00:00 candle is missing.
    with pytest.raises(KrakenImportError, match="recent closed 4h candle"):
        common_timerange(tmp_path, now=datetime(2026, 7, 23, 4, 30, tzinfo=UTC))

    # The comparison does not need that candle because its end is exclusive.
    validate_prepared_data("20260125-20260723", tmp_path)

    # Advancing the requested end by one day correctly requires additional candles.
    with pytest.raises(ValueError, match="outside prepared Kraken coverage"):
        validate_prepared_data("20260125-20260724", tmp_path)
