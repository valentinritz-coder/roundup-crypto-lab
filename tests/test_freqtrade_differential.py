import json
import os
import subprocess
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from roundup_crypto_lab.active_backtests import CapitalMode
from roundup_crypto_lab.freqtrade_active import run_freqtrade_strategy
from roundup_crypto_lab.freqtrade_differential import (
    assert_final_balances_equivalent,
    assert_lifecycle_equivalent,
    generate_single_pair_config,
    validate_execution_scope,
)
from roundup_crypto_lab.investment_plan import InvestmentPlan

ROOT = Path(__file__).resolve().parents[1]
PAIR, STRATEGY, TIMEFRAME = "BTC/EUR", "RoundupBreakoutStrategy", "4h"
FEE, INITIAL = Decimal("0.005"), Decimal("100")
TIMERANGE = "20260121-20260126"


def normalize_native_exit_reason(reason: object) -> str:
    """Map only native exit reasons exercised by the supported adapter scope."""
    mapping = {
        "exit_signal": "exit_signal",
        # Freqtrade exports the repository strategy's exit tag as the reason.
        "close_below_sma20": "exit_signal",
        "stop_loss": "stop_loss",
        "trailing_stop_loss": "stop_loss",
    }
    value = str(reason)
    try:
        return mapping[value]
    except KeyError:
        raise AssertionError(f"unsupported native Freqtrade exit reason: {value!r}") from None


def fixture_frame() -> pd.DataFrame:
    """120 warm-up candles, a signal exit, then a fixed-stop exit."""
    dates = pd.date_range("2026-01-01", periods=150, freq="4h", tz="UTC")
    frame = pd.DataFrame(
        {"date": dates, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0}
    )
    # Breakout at 120 enters at 121. Close 98 at 122 is an exit signal; its
    # 97 low remains above the -12% stop, so it exits normally at 98 on 123.
    frame.loc[120, ["open", "high", "low", "close"]] = (110, 111, 109, 110)
    frame.loc[121, ["open", "high", "low", "close"]] = (110, 111, 109, 110)
    frame.loc[122, ["open", "high", "low", "close"]] = (98, 99, 97, 98)
    frame.loc[123, ["open", "high", "low", "close"]] = (98, 99, 98, 98)
    # A second breakout enters at 125; its intrabar low reaches the 105.6 stop.
    frame.loc[124, ["open", "high", "low", "close"]] = (120, 121, 119, 120)
    frame.loc[125, ["open", "high", "low", "close"]] = (120, 121, 100, 120)
    return frame


def _offline_ccxt_shim(directory: Path) -> None:
    """Prevent only CCXT metadata I/O; native Freqtrade backtesting still runs."""
    (directory / "sitecustomize.py").write_text(
        "import ccxt\nimport ccxt.async_support as accxt\n"
        "m={'BTC/EUR':{'id':'XBTEUR','symbol':'BTC/EUR','base':'BTC','quote':'EUR',"
        "'baseId':'XBT','quoteId':'EUR','type':'spot','spot':True,'swap':False,'future':False,"
        "'option':False,'active':True,'precision':{'amount':1e-8,'price':0.1},"
        "'limits':{'amount':{'min':1e-8,'max':None},'price':{'min':None,'max':None},"
        "'cost':{'min':1,'max':None}},'maker':.005,'taker':.005,'info':{}}}\n"
        "def f(self,reload=False,params={}):\n"
        " self.markets=m; self.markets_by_id={'XBTEUR':[m['BTC/EUR']]}; return m\n"
        "async def a(self,reload=False,params={}): return f(self,reload,params)\n"
        "ccxt.kraken.load_markets=f\naccxt.kraken.load_markets=a\n",
        encoding="utf-8",
    )


def _native_result(config: Path, datadir: Path, output: Path, shim: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "freqtrade",
        "backtesting",
        "--config",
        str(config),
        "--datadir",
        str(datadir),
        "--strategy-path",
        str(ROOT / "user_data/strategies"),
        "--strategy",
        STRATEGY,
        "--timeframe",
        TIMEFRAME,
        "--timerange",
        TIMERANGE,
        "--fee",
        str(FEE),
        "--export",
        "trades",
        "--export-directory",
        str(output),
    ]
    environment = {
        **os.environ,
        "PYTHONPATH": f"{shim}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, env=environment)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            f"native Freqtrade failed\nstdout:\n{exc.stdout}\nstderr:\n{exc.stderr}"
        ) from exc
    archive = next(output.glob("backtest-result-*.zip"), None)
    assert archive is not None, "native Freqtrade produced no machine-readable export"
    with zipfile.ZipFile(archive) as zipped:
        name = next(
            name for name in zipped.namelist() if name.endswith(".json") and "_config" not in name
        )
        return json.loads(zipped.read(name))["strategy"][STRATEGY]


def _native_schema(result: dict[str, object]) -> dict[str, object]:
    trades = []
    for trade in result["trades"]:  # type: ignore[index]
        trades.append(
            {
                "entry_timestamp": trade["open_date"].replace(" ", "T"),
                "exit_timestamp": trade["close_date"].replace(" ", "T"),
                "entry_price": Decimal(str(trade["open_rate"])),
                "exit_price": Decimal(str(trade["close_rate"])),
                "entry_gross_stake": Decimal(str(trade["stake_amount"])),
                "quantity": Decimal(str(trade["amount"])),
                "entry_fee": Decimal(str(trade["stake_amount"])) * Decimal(str(trade["fee_open"])),
                "exit_fee": Decimal(str(trade["amount"]))
                * Decimal(str(trade["close_rate"]))
                * Decimal(str(trade["fee_close"])),
                "exit_reason": normalize_native_exit_reason(trade["exit_reason"]),
            }
        )
    # Closed-only fixture: Freqtrade's final balance is initial capital plus export profit.
    equity = INITIAL + Decimal(str(result["profit_total_abs"]))
    return {
        "trades": trades,
        "free_cash": equity,
        "crypto_value": Decimal("0"),
        "final_equity": equity,
    }


def _adapter_schema(result: dict[str, object]) -> dict[str, object]:
    curve = result["equity_curve"][-1]  # type: ignore[index]
    # Freqtrade serializes exported stake to eight decimal places. Normalize
    # the adapter to that export representation before its strict comparison.
    trades = []
    for trade in result["trades"]:  # type: ignore[index]
        normalized = dict(trade)
        normalized["entry_gross_stake"] = Decimal(str(trade["entry_gross_stake"])).quantize(
            Decimal("0.00000001")
        )
        trades.append(normalized)
    return {
        "trades": trades,
        "free_cash": curve["free_cash"],
        "crypto_value": curve["crypto_value"],
        "final_equity": curve["equity"],
    }


@pytest.mark.native_differential
def test_native_and_adapter_execute_the_same_offline_one_shot_scope(tmp_path: Path) -> None:
    pytest.importorskip("freqtrade")
    frame = fixture_frame()
    datadir = tmp_path / "data" / "kraken"
    datadir.mkdir(parents=True)
    data_file = datadir / "BTC_EUR-4h.feather"
    frame.to_feather(data_file)
    config_path = tmp_path / "native.json"
    metadata = generate_single_pair_config(
        ROOT / "user_data/config.json",
        config_path,
        PAIR,
        overrides={"dry_run_wallet": int(INITIAL), "fee": float(FEE), "use_custom_stoploss": False},
    )
    config = json.loads(config_path.read_text())
    validate_execution_scope(
        pair=PAIR, data_file=data_file, strategy_timeframe=TIMEFRAME, config=config
    )
    shim = tmp_path / "shim"
    shim.mkdir()
    _offline_ccxt_shim(shim)
    native = _native_schema(_native_result(config_path, datadir, tmp_path / "native", shim))
    adapter = _adapter_schema(
        run_freqtrade_strategy(
            frame,
            InvestmentPlan(INITIAL, "40", FEE, 15),
            STRATEGY,
            ROOT / "user_data/strategies",
            frame.iloc[120]["date"].to_pydatetime(),
            (frame.iloc[-1]["date"] + pd.Timedelta(hours=4)).to_pydatetime(),
            mode=CapitalMode.ONE_SHOT_CAPITAL,
            pair=PAIR,
            config_file=config_path,
        )
    )
    assert metadata["selected_pair"] == PAIR
    assert {trade["exit_reason"] for trade in native["trades"]} == {"exit_signal", "stop_loss"}
    assert_lifecycle_equivalent(native["trades"], adapter["trades"])
    assert_final_balances_equivalent(native, adapter)


@pytest.mark.parametrize(
    ("reason", "normalized"),
    [
        ("exit_signal", "exit_signal"),
        ("close_below_sma20", "exit_signal"),
        ("stop_loss", "stop_loss"),
        ("trailing_stop_loss", "stop_loss"),
    ],
)
def test_normalize_native_exit_reason_accepts_only_documented_reasons(
    reason: str, normalized: str
) -> None:
    assert normalize_native_exit_reason(reason) == normalized


@pytest.mark.parametrize("reason", ["roi", "force_exit", "new_reason", ""])
def test_normalize_native_exit_reason_rejects_unknown_reasons(reason: str) -> None:
    with pytest.raises(AssertionError, match="unsupported native Freqtrade exit reason"):
        normalize_native_exit_reason(reason)


def _trade(**overrides: object) -> dict[str, object]:
    trade = {
        "entry_timestamp": "2026-01-01T00:00:00+00:00",
        "exit_timestamp": "2026-01-01T04:00:00+00:00",
        "entry_price": Decimal("100"),
        "exit_price": Decimal("101"),
        "entry_gross_stake": Decimal("80"),
        "quantity": Decimal("0.8"),
        "entry_fee": Decimal("0.4"),
        "exit_fee": Decimal("0.404"),
        "exit_reason": "exit_signal",
    }
    trade.update(overrides)
    return trade


@pytest.mark.parametrize(
    ("field", "value", "passes"),
    [
        ("quantity", Decimal("0.80000001"), True),
        ("quantity", Decimal("0.800000011"), False),
        ("entry_fee", Decimal("0.40000001"), True),
        ("entry_fee", Decimal("0.400000011"), False),
        ("entry_price", Decimal("100.000000001"), False),
        ("exit_price", Decimal("101.000000001"), False),
        ("entry_gross_stake", Decimal("80.000000001"), False),
        ("exit_reason", "stop_loss", False),
    ],
)
def test_lifecycle_comparison_tolerates_only_export_derived_values(
    field: str, value: object, passes: bool
) -> None:
    native = _trade()
    adapter = _trade(**{field: value})
    if passes:
        assert_lifecycle_equivalent([native], [adapter])
    else:
        with pytest.raises(AssertionError):
            assert_lifecycle_equivalent([native], [adapter])
