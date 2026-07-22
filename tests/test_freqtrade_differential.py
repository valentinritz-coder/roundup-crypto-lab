import json
from decimal import Decimal
from pathlib import Path

import pytest

from roundup_crypto_lab.freqtrade_differential import (
    assert_final_balances_equivalent,
    assert_lifecycle_equivalent,
    config_digest,
    generate_single_pair_config,
    validate_execution_scope,
)

ROOT = Path(__file__).resolve().parents[1]


def test_generated_native_config_has_exactly_one_selected_pair(tmp_path: Path) -> None:
    destination = tmp_path / "native-btc.json"
    metadata = generate_single_pair_config(ROOT / "user_data/config.json", destination, "BTC/EUR")

    generated = json.loads(destination.read_text(encoding="utf-8"))
    assert generated["exchange"]["pair_whitelist"] == ["BTC/EUR"]
    assert metadata["selected_pair"] == "BTC/EUR"
    assert metadata["config_digest"] == config_digest(generated)
    # Generation is a copy; the committed two-pair research config is untouched.
    original = json.loads((ROOT / "user_data/config.json").read_text(encoding="utf-8"))
    assert original["exchange"]["pair_whitelist"] == ["BTC/EUR", "ETH/EUR"]


def test_execution_scope_rejects_pair_or_timeframe_drift(tmp_path: Path) -> None:
    path = tmp_path / "BTC_EUR-4h.feather"
    metadata = generate_single_pair_config(
        ROOT / "user_data/config.json", tmp_path / "native.json", "BTC/EUR"
    )
    config = json.loads(Path(metadata["generated_config"]).read_text(encoding="utf-8"))
    validate_execution_scope(pair="BTC/EUR", data_file=path, strategy_timeframe="4h", config=config)
    config["exchange"]["pair_whitelist"] = ["BTC/EUR", "ETH/EUR"]
    with pytest.raises(ValueError, match="exactly the selected pair"):
        validate_execution_scope(
            pair="BTC/EUR", data_file=path, strategy_timeframe="4h", config=config
        )


def test_differential_comparison_rejects_unexplained_lifecycle_divergence() -> None:
    trade = {
        "entry_timestamp": "2026-01-01T00:00:00+00:00",
        "exit_timestamp": "2026-01-02T00:00:00+00:00",
        "entry_price": "100",
        "exit_price": "105",
        "entry_gross_stake": "80",
        "quantity": "0.796",
        "entry_fee": "0.4",
        "exit_fee": "0.4179",
        "exit_reason": "exit_signal",
    }
    nearly_identical = dict(trade, quantity="0.796000001")
    assert_lifecycle_equivalent([trade], [nearly_identical])
    with pytest.raises(AssertionError, match="exit_reason differs"):
        assert_lifecycle_equivalent([trade], [dict(trade, exit_reason="stop_loss")])
    assert_final_balances_equivalent(
        {"free_cash": "100", "crypto_value": "0", "final_equity": "100"},
        {"free_cash": Decimal("100"), "crypto_value": Decimal("0"), "final_equity": Decimal("100")},
    )
