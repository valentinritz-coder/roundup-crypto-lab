import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "user_data" / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_freqtrade_configuration_is_dry_spot_and_single_position() -> None:
    config = load_config()

    assert config["dry_run"] is True
    assert config["trading_mode"] == "spot"
    assert config["margin_mode"] == ""
    assert config["max_open_trades"] == 1
    assert config["stake_currency"] == "EUR"
    assert config["timeframe"] == "4h"
    assert config["strategy_path"] == "user_data/strategies"


def test_initial_pair_scope_and_credentials_are_restricted() -> None:
    exchange = load_config()["exchange"]

    assert exchange["name"] == "kraken"
    assert exchange["pair_whitelist"] == ["BTC/EUR", "ETH/EUR"]
    assert exchange["key"] == ""
    assert exchange["secret"] == ""


def test_strategy_is_explicitly_long_only() -> None:
    strategy = (ROOT / "user_data" / "strategies" / "RoundupBreakoutStrategy.py").read_text(
        encoding="utf-8"
    )

    assert "can_short = False" in strategy
    assert 'timeframe = "4h"' in strategy
    assert ".rolling(20).max().shift(1)" in strategy
