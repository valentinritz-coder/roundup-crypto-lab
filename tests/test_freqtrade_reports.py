import json
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


def test_recursive_accepts_stable_report(tmp_path: Path) -> None:
    validate_recursive_report(
        write(
            tmp_path / "stable.txt", "No variance on indicator(s) found due to recursive formula.\n"
        )
    )


def result(
    trades: int = 2, profit: float = -31.7, strategy: str = "RoundupBreakoutStrategy"
) -> dict:
    return {
        "strategy": {
            strategy: {
                "total_trades": trades,
                "wins": 1,
                "losses": 1,
                "winrate": 0.5,
                "profit_total_pct": profit,
                "profit_total_abs": -63.4,
                "profit_factor": 0.5,
                "max_drawdown_account": 0.317,
                "starting_balance": 200,
                "final_balance": 136.6,
            }
        }
    }


def make_summary(tmp_path: Path, data: dict) -> Path:
    source = write(tmp_path / "backtest.json", json.dumps(data))
    output = tmp_path / "summary.json"
    create_baseline_summary(
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
    return output


def test_baseline_summary_valid_and_rejects_invalid_results(tmp_path: Path) -> None:
    summary = make_summary(tmp_path, result())
    validate_baseline_summary(summary)
    with pytest.raises(ValueError, match="no strategy"):
        make_summary(tmp_path, result(strategy="Other"))
    with pytest.raises(ValueError, match="zero trades"):
        make_summary(tmp_path, result(trades=0))
    with pytest.raises(ValueError, match="non-finite"):
        make_summary(tmp_path, result(profit=float("inf")))
