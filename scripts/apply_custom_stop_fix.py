from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected block in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace(
    "src/roundup_crypto_lab/freqtrade_active.py",
    '''            # Candle N can use ATR from completed candle N-1, never its own OHLC.
            prior_atr = analyzed.iloc[index - 1].get("atr_14") if index else None
            atr = None if pd.isna(prior_atr) else Decimal(str(prior_atr))
''',
    '''            # Freqtrade custom_stoploss sees the analyzed current candle during
            # backtesting. Its current_rate is the candle high for a long trade.
            current_atr = analyzed.iloc[index].get("atr_14")
            atr = None if pd.isna(current_atr) else Decimal(str(current_atr))
''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''    """One OHLC snapshot. ``atr`` was computed from the prior completed candle."""
''',
    '''    """One OHLC snapshot with the ATR visible to Freqtrade for this candle."""
''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''    gap below the stop fills at open; otherwise a low touching it fills at the
    stop. ATR stops are raised from prior-completed-candle ATR only and never
    lowered. Fees apply to both entry and exit notional.
''',
    '''    gap below the stop fills at open; otherwise a low touching it fills at the
    stop. In backtesting, a long custom stop uses the candle high as current_rate,
    the current analyzed ATR, and is evaluated against that candle's low. Stops
    can only tighten. Fees apply to both entry and exit notional.
''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''    def close_trade(candle: Candle, price: Decimal, reason: str, tag: str | None = None) -> None:
        nonlocal cash, quantity, fees, current_deployed, open_trade, stop_price
        assert open_trade is not None
        gross = quantity * price
        fee = gross * plan.fee_ratio
        cash += gross - fee
        fees += fee
        open_trade.update(
            {
                "exit_timestamp": candle.timestamp.astimezone(UTC).isoformat(),
                "exit_price": price,
                "exit_fee": fee,
                "exit_reason": reason,
                "exit_tag": tag,
                "net_proceeds": gross - fee,
                "total_fees": open_trade["entry_fee"] + fee,
            }
        )
        quantity = Decimal("0")
        current_deployed = Decimal("0")
        open_trade = None
        stop_price = None

''',
    '''    def close_trade(candle: Candle, price: Decimal, reason: str, tag: str | None = None) -> None:
        nonlocal cash, quantity, fees, current_deployed, open_trade, stop_price
        assert open_trade is not None
        gross = quantity * price
        fee = gross * plan.fee_ratio
        cash += gross - fee
        fees += fee
        open_trade.update(
            {
                "exit_timestamp": candle.timestamp.astimezone(UTC).isoformat(),
                "exit_price": price,
                "exit_fee": fee,
                "exit_reason": reason,
                "exit_tag": tag,
                "net_proceeds": gross - fee,
                "total_fees": open_trade["entry_fee"] + fee,
            }
        )
        quantity = Decimal("0")
        current_deployed = Decimal("0")
        open_trade = None
        stop_price = None

    def update_custom_stop(candle: Candle) -> None:
        nonlocal stop_price
        if (
            not quantity
            or not lifecycle.use_custom_stoploss
            or candle.atr is None
            or open_trade is None
        ):
            return
        current_rate = candle.high or candle.open
        candidate = current_rate - lifecycle.atr_stop_multiplier * candle.atr  # type: ignore[operator]
        previous = stop_price
        stop_price = max(stop_price or candidate, candidate)
        updates = open_trade.setdefault("stop_updates", [])
        assert isinstance(updates, list)
        updates.append(
            {
                "timestamp": candle.timestamp.astimezone(UTC).isoformat(),
                "current_rate": current_rate,
                "atr": candle.atr,
                "candidate_stop_price": candidate,
                "stop_price_before": previous,
                "stop_price_after": stop_price,
            }
        )

''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''        # ATR was exposed only from a completed predecessor candle by the bridge.
        if quantity and lifecycle.use_custom_stoploss and candle.atr is not None:
            candidate = candle.open - lifecycle.atr_stop_multiplier * candle.atr  # type: ignore[operator]
            stop_price = max(stop_price or candidate, candidate)
        decision = decide(WalletState(timestamp, cash, quantity, quantity > 0))
''',
    '''        update_custom_stop(candle)
        decision = decide(WalletState(timestamp, cash, quantity, quantity > 0))
''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''                "initial_stop_price": stop_price,
                "exit_timestamp": None,
''',
    '''                "initial_stop_price": stop_price,
                "stop_updates": [],
                "exit_timestamp": None,
''',
)
replace(
    "src/roundup_crypto_lab/active_backtests.py",
    '''            trades.append(open_trade)
            # Native backtesting can fill a newly opened position's stop from
            # the entry candle's remaining intrabar range.
            if low <= stop_price:
''',
    '''            trades.append(open_trade)
            # Freqtrade calls custom_stoploss after the fill too. The resulting
            # stop is evaluated against the entry candle's remaining range.
            update_custom_stop(candle)
            if low <= stop_price:
''',
)
replace(
    "tests/test_active_backtests.py",
    '''    # At day 2 ATR raises 88 to 100; day 3's wider ATR cannot lower it.
    assert result["trades"][0]["exit_price"] == Decimal("100")
''',
    '''    # Freqtrade uses each candle high as current_rate. Day 2 raises 88 to
    # 101; day 3's wider ATR cannot lower it.
    assert result["trades"][0]["exit_price"] == Decimal("101")
    updates = result["trades"][0]["stop_updates"]
    assert updates[0]["current_rate"] == Decimal("111")
    assert updates[0]["atr"] == Decimal("5")
    assert updates[0]["candidate_stop_price"] == Decimal("101")
''',
)
append = '''\n\ndef test_custom_stop_is_applied_on_entry_candle_using_high_and_current_atr() -> None:\n    result = run_active_backtest(\n        [ohlc(1, "100", "110", "90", "105", "5")],\n        plan(),\n        at(1),\n        at(2),\n        lambda state: StrategyDecision(Action.BUY, state.cash),\n        lifecycle=lifecycle(custom=True),\n    )\n    trade = result["trades"][0]\n    assert trade["initial_stop_price"] == Decimal("88")\n    assert trade["stop_updates"] == [\n        {\n            "timestamp": "2026-01-01T00:00:00+00:00",\n            "current_rate": Decimal("110"),\n            "atr": Decimal("5"),\n            "candidate_stop_price": Decimal("100"),\n            "stop_price_before": Decimal("88"),\n            "stop_price_after": Decimal("100"),\n        }\n    ]\n    assert trade["exit_price"] == Decimal("100")\n    assert trade["exit_reason"] == "stop_loss"\n'''
test_path = Path("tests/test_active_backtests.py")
test_path.write_text(test_path.read_text(encoding="utf-8") + append, encoding="utf-8")

replace(
    "docs/active-backtests.md",
    '''3. The fixed strategy stop starts at entry price times `1 + stoploss`. The supported custom stop is the repository's `open - 2 × ATR14`; ATR is taken only from the prior completed candle and a stop can only tighten.
''',
    '''3. The fixed strategy stop starts at entry price times `1 + stoploss`. During backtesting Freqtrade supplies the candle high as `current_rate` for long custom stops, and the repository strategy computes `high - 2 × ATR14` from the current analyzed candle. The resulting stop is tested against that candle's low and can only tighten.
''',
)

# Trigger marker: workflow file now exists on the branch.
