import json
import zipfile
from pathlib import Path

import pytest

from roundup_crypto_lab.freqtrade_reports import (
    create_baseline_summary,
    validate_baseline_summary,
    validate_lookahead_report,
    validate_recursive_report,
)


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


LOOKAHEAD = (
    "strategy,has_bias,biased_entry_signals,biased_exit_signals,biased_indicators\n"
    "RoundupBreakoutStrategy,False,0,0,\n"
)


def test_lookahead_rejects_absent_empty_columns_and_strategy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing"):
        validate_lookahead_report(tmp_path / "missing.csv")
    with pytest.raises(ValueError, match="empty"):
        validate_lookahead_report(write(tmp_path / "empty.csv", ""))
    with pytest.raises(ValueError, match="required"):
        validate_lookahead_report(
            write(tmp_path / "columns.csv", "strategy\nRoundupBreakoutStrategy\n")
        )
    with pytest.raises(ValueError, match="no row"):
        validate_lookahead_report(
            write(tmp_path / "other.csv", LOOKAHEAD.replace("RoundupBreakoutStrategy", "Other"))
        )


def test_lookahead_accepts_clean_and_rejects_entry_exit_bias(tmp_path: Path) -> None:
    validate_lookahead_report(write(tmp_path / "clean.csv", LOOKAHEAD))
    with pytest.raises(ValueError, match="bias"):
        validate_lookahead_report(
            write(tmp_path / "entry.csv", LOOKAHEAD.replace(",0,0,", ",1,0,"))
        )
    with pytest.raises(ValueError, match="bias"):
        validate_lookahead_report(write(tmp_path / "exit.csv", LOOKAHEAD.replace(",0,0,", ",0,1,")))


def test_recursive_rejects_absent_and_missing_startups_and_instability(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing"):
        validate_recursive_report(tmp_path / "missing.txt")
    with pytest.raises(ValueError, match="missing startup"):
        validate_recursive_report(
            write(tmp_path / "short.txt", "Recursive Analysis\n│ Indicators │ 120 │ 240 │\n")
        )
    unstable = (
        "Recursive Analysis\n│ Indicators │ 120 │ 240 │ 480 │\n│ sma │ 0.000% │ 0.100% │ 0.000% │\n"
    )
    with pytest.raises(ValueError, match="unstable"):
        validate_recursive_report(write(tmp_path / "unstable.txt", unstable))


def test_recursive_accepts_freqtrade_2026_6_heavy_vertical_table(tmp_path: Path) -> None:
    report = (
        "                    Recursive Analysis\n"
        "┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓\n"
        "┃ Indicators ┃ 120 (from strategy) ┃     240 ┃     480 ┃\n"
        "┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩\n"
        "┃ sma_20     ┃              0.000% ┃  0.000% ┃  0.000% ┃\n"
        "└────────────┴─────────────────────┴─────────┴─────────┘\n"
    )
    validate_recursive_report(write(tmp_path / "rich-heavy.txt", report))


def test_recursive_accepts_stable_report(tmp_path: Path) -> None:
    validate_recursive_report(
        write(
            tmp_path / "stable.txt", "No variance on indicator(s) found due to recursive formula.\n"
        )
    )


def real_result(strategy: str = "RoundupBreakoutStrategy") -> dict:
    """Minimal Freqtrade 2026.6 result schema observed in the workflow artifact."""
    return {
        "strategy": {
            strategy: {
                "total_trades": 32,
                "wins": 5,
                "losses": 27,
                "winrate": 0.15625,
                "profit_total": -0.3169686309,
                "profit_total_abs": -63.39372618,
                "profit_factor": 0.1756020735445459,
                "max_drawdown_account": 0.3169686309,
                "starting_balance": 200,
                "final_balance": 136.60627381999998,
            }
        }
    }


def make_summary(tmp_path: Path, data: dict) -> tuple[Path, dict]:
    source = write(tmp_path / "backtest.json", json.dumps(data))
    output = tmp_path / "summary.json"
    summary = create_baseline_summary(
        source,
        output,
        timerange="x",
        pairs=["BTC/EUR"],
        timeframe="4h",
        freqtrade_version="2026.6",
        freqtrade_commit="abc",
        repository_commit="def",
        cache_manifest=tmp_path / "manifest.json",
    )
    return output, summary


def test_baseline_summary_uses_real_freqtrade_2026_6_ratio_fields(tmp_path: Path) -> None:
    output, summary = make_summary(tmp_path, real_result())
    validate_baseline_summary(output)
    assert "profit_total_pct" not in real_result()["strategy"]["RoundupBreakoutStrategy"]
    assert summary["trades"] == 32
    assert summary["total_profit_ratio"] == pytest.approx(-0.3169686309)
    assert summary["total_profit_pct"] == pytest.approx(-31.69686309)
    assert summary["win_rate_ratio"] == pytest.approx(0.15625)
    assert summary["win_rate_pct"] == pytest.approx(15.625)
    assert summary["max_drawdown_pct"] == pytest.approx(31.69686309)
    assert summary["total_profit_abs"] == pytest.approx(-63.39372618)


def test_baseline_summary_rejects_missing_real_field_and_invalid_results(tmp_path: Path) -> None:
    missing_profit = real_result()
    del missing_profit["strategy"]["RoundupBreakoutStrategy"]["profit_total"]
    with pytest.raises(ValueError, match="required"):
        make_summary(tmp_path, missing_profit)
    with pytest.raises(ValueError, match="no strategy"):
        make_summary(tmp_path, real_result(strategy="Other"))
    zero_trades = real_result()
    zero_trades["strategy"]["RoundupBreakoutStrategy"]["total_trades"] = 0
    with pytest.raises(ValueError, match="zero trades"):
        make_summary(tmp_path, zero_trades)
    non_finite = real_result()
    non_finite["strategy"]["RoundupBreakoutStrategy"]["profit_total"] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        make_summary(tmp_path, non_finite)


def test_backtest_zip_selects_only_primary_result_json(tmp_path: Path) -> None:
    archive = tmp_path / "backtest.zip"
    with zipfile.ZipFile(archive, "w") as result_zip:
        result_zip.writestr("backtest-result-2026.json", json.dumps(real_result()))
        result_zip.writestr("backtest-result-2026_config.json", json.dumps({"strategy": "config"}))
        result_zip.writestr("backtest-result-2026.meta.json", json.dumps({"meta": True}))
    output = tmp_path / "summary.json"
    summary = create_baseline_summary(
        archive,
        output,
        timerange="x",
        pairs=["BTC/EUR"],
        timeframe="4h",
        freqtrade_version="2026.6",
        freqtrade_commit="abc",
        repository_commit="def",
        cache_manifest=tmp_path / "manifest.json",
    )
    assert summary["trades"] == 32


def test_backtest_zip_rejects_missing_or_ambiguous_primary_result(tmp_path: Path) -> None:
    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as result_zip:
        result_zip.writestr("backtest-result_config.json", json.dumps({"strategy": {}}))
    ambiguous = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(ambiguous, "w") as result_zip:
        result_zip.writestr("backtest-result-a.json", json.dumps(real_result()))
        result_zip.writestr("backtest-result-b.json", json.dumps(real_result()))
    output = tmp_path / "summary.json"
    kwargs = {
        "timerange": "x",
        "pairs": ["BTC/EUR"],
        "timeframe": "4h",
        "freqtrade_version": "2026.6",
        "freqtrade_commit": "abc",
        "repository_commit": "def",
        "cache_manifest": tmp_path / "manifest.json",
    }
    with pytest.raises(ValueError, match="no primary"):
        create_baseline_summary(missing, output, **kwargs)
    with pytest.raises(ValueError, match="ambiguous"):
        create_baseline_summary(ambiguous, output, **kwargs)
