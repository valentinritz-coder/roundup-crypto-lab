import json
import zipfile
from pathlib import Path

from roundup_crypto_lab.one_shot_diagnostic import diagnose
from roundup_crypto_lab.one_shot_diagnostic_runner import register_native_exit_reasons


def test_end_of_window_force_exit_becomes_structured_failed_diagnostic(tmp_path: Path) -> None:
    strategy = "RoundupTrendPullbackStrategy"
    native_zip = tmp_path / "native.zip"
    with zipfile.ZipFile(native_zip, "w") as archive:
        archive.writestr(
            "backtest-result.json",
            json.dumps(
                {
                    "strategy": {
                        strategy: {
                            "trades": [
                                {
                                    "open_date": "2026-07-22T12:00:00+00:00",
                                    "close_date": "2026-07-22T20:00:00+00:00",
                                    "open_rate": "100",
                                    "close_rate": "110",
                                    "stake_amount": "40",
                                    "amount": "0.4",
                                    "fee_open": "0.0026",
                                    "fee_close": "0.0026",
                                    "exit_reason": "force_exit",
                                }
                            ],
                            "final_balance": "43.77",
                        }
                    }
                }
            ),
        )

    active_path = tmp_path / "active.json"
    active_path.write_text(
        json.dumps(
            {
                "experiment": {
                    "strategy": strategy,
                    "experiment_id": "experiment",
                    "selected_pair": "BTC/EUR",
                    "timeframe": "4h",
                    "timerange": "20260125-20260723",
                    "capital_mode": "one_shot_capital",
                },
                "adapter_metrics": {
                    "free_cash": "0",
                    "crypto_value": "43.77",
                    "final_equity": "43.77",
                    "open_position_state": "open_marked_at_final_close",
                },
                "trade_ledger": [
                    {
                        "entry_timestamp": "2026-07-22T12:00:00+00:00",
                        "exit_timestamp": None,
                        "entry_price": "100",
                        "exit_price": None,
                        "entry_gross_stake": "40",
                        "quantity": "0.4",
                        "entry_fee": "0.104",
                        "exit_fee": None,
                        "exit_reason": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    register_native_exit_reasons()
    result = diagnose(native_zip, active_path, strategy)

    row = result["strategies"][0]
    assert row["status"] == "failed"
    assert "active position to be closed" in row["error"]
    assert row["diagnostics"]["native_trade_count"] == 1
    assert row["diagnostics"]["adapter_trade_count"] == 1
    assert row["diagnostics"]["native_trade"]["exit_reason"] == "force_exit"
    assert row["diagnostics"]["adapter_trade"]["exit_reason"] is None
