import json
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.one_shot_diagnostic import (
    _economic_warnings,
    _first_trade_difference,
    combine,
)


def test_first_trade_difference_reports_count_and_context() -> None:
    native = [{"entry_timestamp": "a"}, {"entry_timestamp": "b"}]
    adapter = [{"entry_timestamp": "a"}]

    result = _first_trade_difference(native, adapter)

    assert result["first_divergent_index"] == 1
    assert result["native_trade_count"] == 2
    assert result["adapter_trade_count"] == 1
    assert result["native_trade"] == {"entry_timestamp": "b"}
    assert result["adapter_trade"] is None


def _trade(**overrides: object) -> dict[str, object]:
    trade: dict[str, object] = {
        "entry_timestamp": "2026-01-01T00:00:00+00:00",
        "exit_timestamp": "2026-01-02T00:00:00+00:00",
        "entry_price": "100",
        "exit_price": "110",
        "entry_gross_stake": "32",
        "quantity": "0.32",
        "entry_fee": "0.08",
        "exit_fee": "0.09",
        "exit_reason": "exit_signal",
    }
    trade.update(overrides)
    return trade


def _comparison(
    native_trade: dict[str, object], adapter_trade: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    expected = {
        "trades": [native_trade],
        "free_cash": Decimal("40"),
        "crypto_value": Decimal("0"),
        "final_equity": Decimal("40"),
    }
    actual = {
        "trades": [adapter_trade],
        "free_cash": Decimal("39.99999999"),
        "crypto_value": Decimal("0"),
        "final_equity": Decimal("39.99999999"),
    }
    return expected, actual


def test_economic_warnings_accept_rounding_but_not_structural_changes() -> None:
    expected, actual = _comparison(
        _trade(),
        _trade(entry_gross_stake="31.99999999", quantity="0.320000001"),
    )
    warnings = _economic_warnings(expected, actual)
    assert warnings is not None
    warning_kinds = {warning["kind"] for warning in warnings}
    assert warning_kinds >= {"rounding", "balance_rounding"}

    expected, actual = _comparison(
        _trade(),
        _trade(exit_timestamp="2026-01-03T00:00:00+00:00"),
    )
    assert _economic_warnings(expected, actual) is None


def test_economic_warnings_accept_bounded_same_candle_stop_model_difference() -> None:
    expected, actual = _comparison(
        _trade(exit_reason="stop_loss", exit_price="63218.9"),
        _trade(exit_reason="stop_loss", exit_price="62779.2"),
    )
    actual["free_cash"] = Decimal("39.08")
    actual["final_equity"] = Decimal("39.08")

    warnings = _economic_warnings(expected, actual)

    assert warnings is not None
    assert any(
        warning["kind"] == "supported_stop_model_difference" for warning in warnings
    )


def test_economic_warnings_reject_large_stop_or_balance_difference() -> None:
    expected, actual = _comparison(
        _trade(exit_reason="stop_loss", exit_price="100"),
        _trade(exit_reason="stop_loss", exit_price="98.9"),
    )
    assert _economic_warnings(expected, actual) is None

    expected, actual = _comparison(
        _trade(exit_reason="stop_loss", exit_price="100"),
        _trade(exit_reason="stop_loss", exit_price="99.5"),
    )
    actual["free_cash"] = Decimal("38.99")
    actual["final_equity"] = Decimal("38.99")
    assert _economic_warnings(expected, actual) is None


def _document(strategy: str, status: str = "passed") -> dict[str, object]:
    row: dict[str, object] = {
        "strategy": strategy,
        "status": status,
        "trade_count": 1,
        "checked_fields": ["lifecycle", "final_balances"],
    }
    if status == "passed_with_warnings":
        row["warnings"] = [{"kind": "rounding"}]
    return {
        "schema_version": "one-shot-differential/v1",
        "experiment_id": "experiment",
        "selected_pair": "BTC/EUR",
        "timeframe": "4h",
        "timerange": "20260123-20260718",
        "capital_mode": "one_shot_capital",
        "strategies": [row],
    }


def test_combine_preserves_failed_diagnostics(tmp_path: Path) -> None:
    paths = []
    for index, strategy in enumerate(STRATEGY_ORDER):
        document = _document(strategy, "failed" if index == 0 else "passed")
        if index == 0:
            document["strategies"][0]["diagnostics"] = {
                "native_trade_count": 25,
                "adapter_trade_count": 19,
            }
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        paths.append(path)

    result = combine(paths)

    assert result["overall_status"] == "failed"
    assert result["strategies"][0]["diagnostics"]["native_trade_count"] == 25


def test_combine_returns_warning_status_without_failing(tmp_path: Path) -> None:
    paths = []
    for index, strategy in enumerate(STRATEGY_ORDER):
        status = "passed_with_warnings" if index == 0 else "passed"
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(_document(strategy, status)), encoding="utf-8")
        paths.append(path)

    result = combine(paths)

    assert result["overall_status"] == "passed_with_warnings"


def test_combine_rejects_incomplete_results(tmp_path: Path) -> None:
    path = tmp_path / "one.json"
    path.write_text(json.dumps(_document(STRATEGY_ORDER[0])), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly seven"):
        combine([path])
