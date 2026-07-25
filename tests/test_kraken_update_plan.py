from datetime import UTC, datetime, timedelta
from pathlib import Path

from roundup_crypto_lab import kraken_update_plan
from roundup_crypto_lab.kraken_update_plan import build_update_plan

NOW = datetime(2026, 7, 25, 16, 30, tzinfo=UTC)
CLOSED = datetime(2026, 7, 25, 16, tzinfo=UTC)


def manifest_ending(end: datetime) -> dict[str, object]:
    return {
        "datasets": [
            {"pair": "BTC/EUR", "last_timestamp": end.isoformat()},
            {"pair": "ETH/EUR", "last_timestamp": end.isoformat()},
        ]
    }


def test_healthy_cache_uses_bounded_recent_overlap(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        kraken_update_plan,
        "load_and_verify_manifest",
        lambda destination: manifest_ending(CLOSED - timedelta(hours=4)),
    )
    monkeypatch.setattr(kraken_update_plan, "gap_diagnostics", lambda destination: [])

    plan = build_update_plan(tmp_path, now=NOW)

    assert plan.mode == "recent_overlap"
    assert plan.days == 8
    assert plan.first_problem_timestamp is None


def test_required_historical_gap_requires_seed_rebuild(monkeypatch, tmp_path: Path) -> None:
    gap_start = datetime(2025, 11, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(
        kraken_update_plan,
        "load_and_verify_manifest",
        lambda destination: manifest_ending(CLOSED - timedelta(hours=4)),
    )
    monkeypatch.setattr(
        kraken_update_plan,
        "gap_diagnostics",
        lambda destination: [
            {
                "pair": "BTC/EUR",
                "start": gap_start,
                "end": gap_start + timedelta(hours=8),
                "candles": 1,
                "intersects_validation": True,
            }
        ],
    )

    plan = build_update_plan(tmp_path, now=NOW)

    assert plan.mode == "historical_rebuild_required"
    assert plan.days is None
    assert plan.first_problem_timestamp == gap_start.isoformat()
    assert "missing 4h candles" in plan.reason


def test_gap_outside_validation_does_not_trigger_historical_backfill(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        kraken_update_plan,
        "load_and_verify_manifest",
        lambda destination: manifest_ending(CLOSED - timedelta(hours=4)),
    )
    monkeypatch.setattr(
        kraken_update_plan,
        "gap_diagnostics",
        lambda destination: [
            {
                "pair": "ETH/EUR",
                "start": datetime(2024, 4, 14, tzinfo=UTC),
                "end": datetime(2024, 4, 14, 8, tzinfo=UTC),
                "candles": 1,
                "intersects_validation": False,
            }
        ],
    )

    plan = build_update_plan(tmp_path, now=NOW)

    assert plan.mode == "recent_overlap"
    assert plan.days == 8


def test_stale_cache_requires_seed_rebuild(monkeypatch, tmp_path: Path) -> None:
    stale_end = CLOSED - timedelta(days=9)
    monkeypatch.setattr(
        kraken_update_plan,
        "load_and_verify_manifest",
        lambda destination: manifest_ending(stale_end),
    )
    monkeypatch.setattr(kraken_update_plan, "gap_diagnostics", lambda destination: [])

    plan = build_update_plan(tmp_path, now=NOW)

    assert plan.mode == "historical_rebuild_required"
    assert plan.days is None
    assert plan.first_problem_timestamp == stale_end.isoformat()
    assert "too stale" in plan.reason
