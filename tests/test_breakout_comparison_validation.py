from pathlib import Path

import pytest

from roundup_crypto_lab import breakout_comparison, kraken_ohlcv


def _manifest(*, gap: str | None = None) -> dict:
    gaps = [] if gap is None else [gap]
    return {
        "datasets": [
            {
                "pair": pair,
                "first_timestamp": "2024-01-01T00:00:00+00:00",
                "last_timestamp": "2025-12-31T20:00:00+00:00",
                "missing_intervals": gaps,
            }
            for pair in ("BTC/EUR", "ETH/EUR")
        ]
    }


def test_validate_prepared_data_accepts_historical_range_with_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        kraken_ohlcv,
        "load_and_verify_manifest",
        lambda _directory: _manifest(),
    )

    breakout_comparison.validate_prepared_data(
        "20241001-20250401",
        Path("unused"),
    )


def test_validate_prepared_data_rejects_missing_warmup_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest()
    for entry in manifest["datasets"]:
        entry["first_timestamp"] = "2024-09-01T00:00:00+00:00"
    monkeypatch.setattr(
        kraken_ohlcv,
        "load_and_verify_manifest",
        lambda _directory: manifest,
    )

    with pytest.raises(ValueError, match="480-candle warm-up"):
        breakout_comparison.validate_prepared_data(
            "20241001-20250401",
            Path("unused"),
        )


def test_validate_prepared_data_ignores_gap_outside_requested_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        kraken_ohlcv,
        "load_and_verify_manifest",
        lambda _directory: _manifest(
            gap="2025-11-01T12:00:00+00:00..2025-11-01T20:00:00+00:00"
        ),
    )

    breakout_comparison.validate_prepared_data(
        "20241001-20250401",
        Path("unused"),
    )


def test_validate_prepared_data_rejects_gap_in_requested_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        kraken_ohlcv,
        "load_and_verify_manifest",
        lambda _directory: _manifest(
            gap="2024-08-01T00:00:00+00:00..2024-08-01T08:00:00+00:00"
        ),
    )

    with pytest.raises(ValueError, match="intersects requested validation history"):
        breakout_comparison.validate_prepared_data(
            "20241001-20250401",
            Path("unused"),
        )
