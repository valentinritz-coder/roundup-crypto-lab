from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def content(name):
    return (ROOT / ".github/workflows" / name).read_text()


def test_workflows_parse_and_cache_roles() -> None:
    seed, update, validation = (
        content(x)
        for x in ("seed-kraken-data.yml", "update-kraken-data.yml", "freqtrade-validation.yml")
    )
    for text in (seed, update, validation):
        yaml.safe_load(text)
        assert "--erase" not in text
    assert "actions/cache/restore@v4" not in seed and "actions/cache/save@v4" in seed
    assert "actions/cache/restore@v4" in update and "actions/cache/save@v4" in update
    assert "actions/cache/restore@v4" in validation and "actions/cache/save@v4" not in validation
    assert all("kraken-ohlcv-v1-" in text for text in (seed, update, validation))


def test_workflow_safety_contract() -> None:
    seed, update, validation = (
        content(x)
        for x in ("seed-kraken-data.yml", "update-kraken-data.yml", "freqtrade-validation.yml")
    )
    assert "--days 8" in update and "--dl-trades" in update
    assert "BTC/EUR ETH/EUR" in update and "--timeframes 4h" in update
    assert "Changing state.*RUNNING" in validation and "PIPESTATUS" not in validation
    assert "path: |\n            artifacts/" in seed


def test_seed_uses_historical_validation_and_catchup_is_open_ended() -> None:
    seed, update, validation = (
        content(x)
        for x in ("seed-kraken-data.yml", "update-kraken-data.yml", "freqtrade-validation.yml")
    )
    assert "historical_timerange" in seed
    assert "common_timerange" in update and "common_timerange" in validation
    assert "--timerange" in update and "--days 8" in update
    assert "strftime('%Y%m%d')" not in update
