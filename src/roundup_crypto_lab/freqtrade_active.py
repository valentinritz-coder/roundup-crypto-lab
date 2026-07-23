"""Bridge real Freqtrade strategy signal methods into the active cash-flow adapter.

The bridge invokes the strategy's own ``populate_*`` methods.  It does not copy
indicators or entry/exit formulae.  A row's signals become executable only on
the following candle open, matching the adapter's completed-candle convention.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    LifecycleSettings,
    StrategyDecision,
    WalletState,
    run_active_backtest,
)
from roundup_crypto_lab.active_comparison import build_active_result, validate_active_result
from roundup_crypto_lab.freqtrade_differential import config_digest, validate_execution_scope
from roundup_crypto_lab.investment_plan import InvestmentPlan
from roundup_crypto_lab.passive_benchmarks import parse_timerange


def _load_strategy(name: str, strategy_dir: Path) -> Any:
    strategy_path = str(strategy_dir.resolve())
    if strategy_path not in sys.path:
        sys.path.insert(0, strategy_path)
    module = importlib.import_module(name)
    strategy_class = getattr(module, name)
    return strategy_class({}) if inspect.signature(strategy_class).parameters else strategy_class()


def validate_pair_data_file(pair: str, data_file: Path) -> None:
    """Reject a prepared candle file that does not belong to the selected pair."""
    expected_name = f"{pair.replace('/', '_')}-4h.feather"
    if pair not in {"BTC/EUR", "ETH/EUR"} or data_file.name != expected_name:
        raise ValueError(f"data file must be {expected_name} for {pair}")


def strategy_decisions(
    frame: pd.DataFrame,
    strategy_name: str,
    strategy_dir: Path,
    tradable_balance_ratio: Decimal,
    pair: str,
    start: datetime,
    end: datetime,
    config: dict[str, Any] | None = None,
) -> tuple[list[Candle], dict[datetime, Action | None], LifecycleSettings]:
    """Call the real strategy and shift completed-row signals to the next open."""
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required <= set(frame.columns):
        raise ValueError("OHLCV frame is missing required columns")
    if not Decimal("0") < tradable_balance_ratio <= Decimal("1"):
        raise ValueError("tradable balance ratio must be in (0, 1]")
    strategy = _load_strategy(strategy_name, strategy_dir)
    warmup = int(strategy.startup_candle_count)
    before = frame[frame["date"] < start]
    if len(before) < warmup:
        raise ValueError(
            f"insufficient warm-up history: need {warmup} candles before timerange start"
        )
    metadata = {"pair": pair}
    analyzed = strategy.populate_indicators(frame.copy(), metadata)
    analyzed = strategy.populate_entry_trend(analyzed, metadata)
    analyzed = strategy.populate_exit_trend(analyzed, metadata)
    candles = []
    for index, row in enumerate(analyzed.itertuples()):
        timestamp = row.date.to_pydatetime().astimezone(UTC)
        if start <= timestamp < end:
            # Freqtrade custom_stoploss sees the analyzed current candle during
            # backtesting. Its current_rate is the candle high for a long trade.
            current_atr = analyzed.iloc[index].get("atr_14")
            atr = None if pd.isna(current_atr) else Decimal(str(current_atr))
            candles.append(
                Candle(
                    timestamp,
                    Decimal(str(row.open)),
                    Decimal(str(row.close)),
                    Decimal(str(row.high)),
                    Decimal(str(row.low)),
                    atr,
                )
            )
    decisions: dict[datetime, Action | None] = {}
    # Signal row N is known after N closes.  Associate it with N+1's open.
    for index in range(len(analyzed) - 1):
        next_at = analyzed.iloc[index + 1]["date"].to_pydatetime().astimezone(UTC)
        row = analyzed.iloc[index]
        if not (start <= next_at < end):
            continue
        if row.get("exit_long", 0) == 1:
            decisions[next_at] = Action.SELL
        elif row.get("enter_long", 0) == 1:
            decisions[next_at] = Action.BUY
    return candles, decisions, _strategy_lifecycle(strategy, config)


_REPOSITORY_ATR_STOP_MULTIPLIERS: dict[str, Decimal] = {
    "RoundupBreakoutStrategy": Decimal("2"),
    "RoundupBreakoutTrendStrategy": Decimal("2"),
    "RoundupBreakoutAtrStrategy": Decimal("2"),
    "RoundupBreakoutAtrVolumeStrategy": Decimal("2"),
    "RoundupTrendPullbackStrategy": Decimal("2"),
    "RoundupConfirmedBreakoutStrategy": Decimal("2"),
    "RoundupVolatilitySqueezeStrategy": Decimal("2"),
}


def _validate_disabled_minimal_roi(value: object) -> None:
    """Accept only the repository's practically-disabled Freqtrade ROI table."""
    try:
        if not isinstance(value, Mapping):
            raise ValueError
        normalized = tuple(
            sorted(
                (
                    Decimal(str(offset)),
                    Decimal(str(profit)),
                )
                for offset, profit in value.items()
            )
        )
        if any(not offset.is_finite() or not profit.is_finite() for offset, profit in normalized):
            raise ValueError
    except (ArithmeticError, ValueError):
        raise ValueError("active adapter does not support active minimal_roi exits") from None
    if normalized != ((Decimal("0"), Decimal("100")),):
        raise ValueError("active adapter does not support active minimal_roi exits")


def _repository_atr_stop_multiplier(strategy: Any) -> Decimal | None:
    """Return the declared ATR stop scope supported by this repository adapter.

    This deliberately recognises only the seven checked-in strategy classes. It
    does not infer custom-stop semantics from Python source and therefore cannot
    claim support for arbitrary Freqtrade ``custom_stoploss`` implementations.
    """
    if not getattr(strategy, "use_custom_stoploss", False):
        return None
    try:
        return _REPOSITORY_ATR_STOP_MULTIPLIERS[type(strategy).__name__]
    except KeyError:
        raise ValueError(
            "active adapter supports only the repository ATR custom_stoploss form"
        ) from None


def _strategy_lifecycle(strategy: Any, config: dict[str, Any] | None = None) -> LifecycleSettings:
    """Resolve supported settings using Freqtrade configuration precedence."""
    if getattr(strategy, "can_short", False):
        raise ValueError("active adapter supports spot long-only strategies only")
    if getattr(strategy, "trailing_stop", False):
        raise ValueError("active adapter does not support trailing_stop")
    effective = config or {}
    _validate_disabled_minimal_roi(effective.get("minimal_roi", strategy.minimal_roi))
    # Freqtrade configuration overrides the strategy toggle.  This permits the
    # deterministic native differential fixture to exercise the shared fixed
    # stop path without changing the repository strategy source.
    multiplier = (
        _repository_atr_stop_multiplier(strategy)
        if effective.get("use_custom_stoploss", getattr(strategy, "use_custom_stoploss", False))
        else None
    )
    return LifecycleSettings(
        Decimal(str(effective.get("stoploss", strategy.stoploss))),
        multiplier is not None,
        multiplier,
        bool(effective.get("use_exit_signal", getattr(strategy, "use_exit_signal", True))),
    )


def _load_effective_config(config_file: Path) -> dict[str, Any]:
    config = json.loads(config_file.read_text(encoding="utf-8"))
    if config.get("trading_mode") != "spot" or config.get("max_open_trades") != 1:
        raise ValueError("active adapter requires config spot trading_mode and max_open_trades = 1")
    if config.get("stake_currency") != "EUR" or config.get("stake_amount") != "unlimited":
        raise ValueError("active adapter requires EUR unlimited stake configuration")
    for feature in ("trailing_stop",):
        if config.get(feature, False):
            raise ValueError(f"active adapter does not support config {feature}")
    return config


def run_freqtrade_strategy(
    frame: pd.DataFrame,
    plan: InvestmentPlan,
    strategy_name: str,
    strategy_dir: Path,
    start: datetime,
    end: datetime,
    *,
    mode: CapitalMode,
    tradable_balance_ratio: Decimal | None = None,
    pair: str = "BTC/EUR",
    config_file: Path = Path("user_data/config.json"),
) -> dict[str, object]:
    """Run a real strategy's causal signals against recurring wallet cash flows."""
    config = _load_effective_config(config_file)
    configured_ratio = Decimal(str(config["tradable_balance_ratio"]))
    if tradable_balance_ratio is not None and tradable_balance_ratio != configured_ratio:
        raise ValueError("tradable_balance_ratio must be resolved from config, not overridden")
    candles, scheduled, lifecycle = strategy_decisions(
        frame, strategy_name, strategy_dir, configured_ratio, pair, start, end, config
    )
    strategy = _load_strategy(strategy_name, strategy_dir)
    if getattr(strategy, "timeframe", None) != config.get("timeframe"):
        raise ValueError("strategy timeframe and config timeframe differ")

    def decide(wallet: WalletState) -> StrategyDecision:
        action = scheduled.get(wallet.timestamp)
        if action is Action.BUY and not wallet.open_position:
            return StrategyDecision(Action.BUY, wallet.cash * configured_ratio)
        if action is Action.SELL and wallet.open_position:
            return StrategyDecision(Action.SELL)
        return StrategyDecision()

    result = run_active_backtest(candles, plan, start, end, decide, mode=mode, lifecycle=lifecycle)
    result.update(
        {
            "strategy": strategy_name,
            "pair": pair,
            "investment_plan": {
                "initial_capital": plan.initial_capital,
                "monthly_budget": plan.monthly_budget,
                "fee_ratio": plan.fee_ratio,
                "contribution_day": plan.contribution_day,
            },
            "signal_execution": "completed candle N signals execute at candle N+1 open",
            "execution_scope": {
                "selected_pair": pair,
                "config_digest": config_digest(config),
                "timeframe": config["timeframe"],
                "generated_config": str(config_file),
            },
        }
    )
    return result


def _json(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-file", required=True, type=Path)
    parser.add_argument("--pair", required=True, choices=["BTC/EUR", "ETH/EUR"])
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--strategy-dir", default="user_data/strategies", type=Path)
    parser.add_argument("--timerange", required=True)
    parser.add_argument(
        "--capital-mode", choices=[mode.value for mode in CapitalMode], required=True
    )
    parser.add_argument("--initial-capital", required=True)
    parser.add_argument("--monthly-budget", required=True)
    parser.add_argument("--contribution-day", required=True, type=int)
    parser.add_argument("--fee", required=True)
    parser.add_argument("--config-file", default="user_data/config.json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    start, end = parse_timerange(args.timerange)
    validate_pair_data_file(args.pair, args.data_file)
    config = _load_effective_config(args.config_file)
    strategy = _load_strategy(args.strategy, args.strategy_dir)
    validate_execution_scope(
        pair=args.pair,
        data_file=args.data_file,
        strategy_timeframe=strategy.timeframe,
        config=config,
    )
    frame = pd.read_feather(args.data_file)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    plan = InvestmentPlan(
        args.initial_capital, args.monthly_budget, args.fee, args.contribution_day
    )
    result = run_freqtrade_strategy(
        frame,
        plan,
        args.strategy,
        args.strategy_dir,
        start,
        end,
        mode=CapitalMode(args.capital_mode),
        pair=args.pair,
        config_file=args.config_file,
    )
    payload = build_active_result(
        result,
        strategy=args.strategy,
        pair=args.pair,
        timeframe=config["timeframe"],
        timerange=args.timerange,
        execution_model="freqtrade-strategy-signals/causal-single-position-v1",
        effective_settings={
            "fee_ratio": args.fee,
            "tradable_balance_ratio": str(config["tradable_balance_ratio"]),
            "stake_amount": config["stake_amount"],
            "stoploss": str(config["stoploss"]),
        },
    )
    validate_active_result(
        payload, strategy=args.strategy, pair=args.pair, capital_mode=args.capital_mode
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, default=_json, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
