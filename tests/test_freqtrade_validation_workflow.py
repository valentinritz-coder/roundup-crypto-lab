from pathlib import Path

WORKFLOW = Path(".github/workflows/freqtrade-validation.yml")


def test_stale_cache_is_deferred_only_for_pull_requests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assessment = workflow.split("- name: Assess prepared Kraken data", 1)[1].split(
        "- name: Install pinned Freqtrade", 1
    )[0]

    assert "id: market" in assessment
    assert "common_timerange" in assessment
    assert "common dataset end is not a recent closed 4h candle" in assessment
    assert '"$EVENT_NAME" = pull_request' in assessment
    assert "ready=false" in assessment
    assert "Update Kraken data" in assessment
    assert 'exit "$status"' in assessment
    assert workflow.count("steps.market.outputs.ready == 'true'") >= 10
