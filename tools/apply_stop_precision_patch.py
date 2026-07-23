from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected block in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "src/roundup_crypto_lab/active_backtests.py",
    "from decimal import Decimal\n",
    "from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal\n",
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''    atr_stop_multiplier: Decimal | None = None\n    use_exit_signal: bool = True\n\n    def __post_init__(self) -> None:\n''',
    '''    atr_stop_multiplier: Decimal | None = None\n    use_exit_signal: bool = True\n    price_tick: Decimal = Decimal("0.00000001")\n    amount_step: Decimal = Decimal("0.00000001")\n\n    def __post_init__(self) -> None:\n''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''        if self.use_custom_stoploss and (\n            self.atr_stop_multiplier is None or self.atr_stop_multiplier <= 0\n        ):\n            raise ValueError("custom stoploss requires a positive ATR multiplier")\n''',
    '''        if self.use_custom_stoploss and (\n            self.atr_stop_multiplier is None or self.atr_stop_multiplier <= 0\n        ):\n            raise ValueError("custom stoploss requires a positive ATR multiplier")\n        if self.price_tick <= 0 or self.amount_step <= 0:\n            raise ValueError("execution precision steps must be positive")\n''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''DecisionProvider = Callable[[WalletState], StrategyDecision]\n\n\ndef _maximum_drawdown''',
    '''DecisionProvider = Callable[[WalletState], StrategyDecision]\n\n\ndef _round_up(value: Decimal, step: Decimal) -> Decimal:\n    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step\n\n\ndef _round_down(value: Decimal, step: Decimal) -> Decimal:\n    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step\n\n\ndef _maximum_drawdown''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''        candidate = current_rate - lifecycle.atr_stop_multiplier * candle.atr  # type: ignore[operator]\n        previous = stop_price\n        stop_price = max(stop_price or candidate, candidate)\n''',
    '''        raw_candidate = current_rate - lifecycle.atr_stop_multiplier * candle.atr  # type: ignore[operator]\n        candidate = _round_up(raw_candidate, lifecycle.price_tick)\n        previous = stop_price\n        stop_price = max(stop_price or candidate, candidate)\n''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''            gross = decision.stake\n            fee = gross * plan.fee_ratio\n            if gross + fee > cash:\n                raise ValueError("buy stake plus fee must not exceed available cash")\n            # Freqtrade's exported stake excludes the entry fee; its filled\n            # amount is therefore stake / entry price while the wallet pays\n            # stake plus fee.\n            quantity = gross / candle.open\n''',
    '''            requested_gross = decision.stake\n            quantity = _round_down(requested_gross / candle.open, lifecycle.amount_step)\n            if quantity <= 0:\n                raise ValueError("buy amount rounds to zero at exchange precision")\n            gross = quantity * candle.open\n            fee = gross * plan.fee_ratio\n            if gross + fee > cash:\n                raise ValueError("buy stake plus fee must not exceed available cash")\n            # Freqtrade truncates the filled base amount to exchange precision,\n            # then derives the effective stake from amount times entry price.\n''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''            stop_price = candle.open * (Decimal("1") + lifecycle.fixed_stoploss)\n''',
    '''            stop_price = _round_up(\n                candle.open * (Decimal("1") + lifecycle.fixed_stoploss), lifecycle.price_tick\n            )\n''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''    the current analyzed ATR, and is evaluated against that candle's low. Stops\n''',
    '''    the ATR visible through Freqtrade's sliced analyzed dataframe, and is evaluated\n    against that candle's low. Stops\n''',
)

replace(
    "src/roundup_crypto_lab/freqtrade_active.py",
    '''            # Freqtrade custom_stoploss sees the analyzed current candle during\n            # backtesting. Its current_rate is the candle high for a long trade.\n            current_atr = analyzed.iloc[index].get("atr_14")\n            atr = None if pd.isna(current_atr) else Decimal(str(current_atr))\n''',
    '''            # Backtesting supplies the current candle high as current_rate, while\n            # get_analyzed_dataframe() is sliced before this execution row. Therefore\n            # the strategy sees ATR14 from the prior completed candle.\n            visible_atr = analyzed.iloc[index - 1].get("atr_14") if index else None\n            atr = None if pd.isna(visible_atr) else Decimal(str(visible_atr))\n''',
)
replace(
    "src/roundup_crypto_lab/freqtrade_active.py",
    '''    return candles, decisions, _strategy_lifecycle(strategy, config)\n\n\n_REPOSITORY_ATR_STOP_MULTIPLIERS''',
    '''    return candles, decisions, _strategy_lifecycle(strategy, config, pair)\n\n\n_KRAKEN_EXECUTION_PRECISION: dict[str, tuple[Decimal, Decimal]] = {\n    "BTC/EUR": (Decimal("0.1"), Decimal("0.00000001")),\n    "ETH/EUR": (Decimal("0.01"), Decimal("0.00000001")),\n}\n\n\n_REPOSITORY_ATR_STOP_MULTIPLIERS''',
)
replace(
    "src/roundup_crypto_lab/freqtrade_active.py",
    '''def _strategy_lifecycle(strategy: Any, config: dict[str, Any] | None = None) -> LifecycleSettings:\n''',
    '''def _strategy_lifecycle(\n    strategy: Any, config: dict[str, Any] | None = None, pair: str | None = None\n) -> LifecycleSettings:\n''',
)
replace(
    "src/roundup_crypto_lab/freqtrade_active.py",
    '''    return LifecycleSettings(\n        Decimal(str(effective.get("stoploss", strategy.stoploss))),\n        multiplier is not None,\n        multiplier,\n        bool(effective.get("use_exit_signal", getattr(strategy, "use_exit_signal", True))),\n    )\n''',
    '''    price_tick, amount_step = _KRAKEN_EXECUTION_PRECISION.get(\n        pair or "", (Decimal("0.00000001"), Decimal("0.00000001"))\n    )\n    return LifecycleSettings(\n        Decimal(str(effective.get("stoploss", strategy.stoploss))),\n        multiplier is not None,\n        multiplier,\n        bool(effective.get("use_exit_signal", getattr(strategy, "use_exit_signal", True))),\n        price_tick,\n        amount_step,\n    )\n''',
)

replace(
    "docs/active-backtests.md",
    '''3. The fixed strategy stop starts at entry price times `1 + stoploss`. During backtesting Freqtrade supplies the candle high as `current_rate` for long custom stops, and the repository strategy computes `high - 2 × ATR14` from the current analyzed candle. The resulting stop is tested against that candle's low and can only tighten.\n''',
    '''3. The fixed strategy stop starts at entry price times `1 + stoploss`. During backtesting Freqtrade supplies the current candle high as `current_rate`, while its sliced analyzed dataframe exposes ATR14 from the prior completed candle. The repository strategy therefore computes `current high - 2 × prior ATR14`; Kraken price precision rounds a long stop upward, and the result can only tighten.\n''',
)
replace(
    "docs/active-backtests.md",
    '''5. Entry and exit fees use the investment plan fee ratio. Closing a trade resets current deployed capital; cumulative gross deployed stays historical.\n''',
    '''5. Before entry, the requested base amount is truncated to Kraken amount precision; effective stake is then `amount × entry price`. Entry and exit fees use that effective notional. Closing a trade resets current deployed capital; cumulative gross deployed stays historical.\n''',
)

path = Path("tests/test_active_backtests.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_kraken_precision_rounds_stop_up_and_amount_down() -> None:\n    precise = LifecycleSettings(\n        Decimal("-0.12"),\n        True,\n        Decimal("2"),\n        True,\n        Decimal("0.1"),\n        Decimal("0.00000001"),\n    )\n    result = run_active_backtest(\n        [\n            Candle(\n                at(1),\n                Decimal("63098.5"),\n                Decimal("63000"),\n                Decimal("63738.5"),\n                Decimal("60931.4"),\n                Decimal("1403.500308265872"),\n            )\n        ],\n        InvestmentPlan("40", "40", "0.0026", 1),\n        at(1),\n        at(2),\n        lambda state: StrategyDecision(Action.BUY, state.cash * Decimal("0.8")),\n        mode=CapitalMode.ONE_SHOT_CAPITAL,\n        lifecycle=precise,\n    )\n    trade = result["trades"][0]\n    assert trade["quantity"] == Decimal("0.00050714")\n    assert trade["entry_gross_stake"] == Decimal("31.999773290")\n    assert trade["stop_updates"][0]["candidate_stop_price"] == Decimal("60931.5")\n    assert trade["exit_price"] == Decimal("60931.5")\n'''
path.write_text(text, encoding="utf-8")

path = Path("tests/test_breakout_strategy_variants.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_kraken_precision_is_attached_to_supported_pair_lifecycle() -> None:\n    lifecycle = _strategy_lifecycle(_LifecycleStub(), pair="BTC/EUR")\n    assert lifecycle.price_tick == Decimal("0.1")\n    assert lifecycle.amount_step == Decimal("0.00000001")\n'''
path.write_text(text, encoding="utf-8")
