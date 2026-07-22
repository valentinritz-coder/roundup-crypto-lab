import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    StrategyDecision,
    run_active_backtest,
)
from roundup_crypto_lab.active_comparison import build_active_result, validate_active_result
from roundup_crypto_lab.investment_plan import InvestmentPlan


def test_versioned_active_artifact_validates_and_rejects_cash_overspend() -> None:
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    candles = [
        Candle(start, Decimal("100"), Decimal("110")),
        Candle(datetime(2026, 1, 15, tzinfo=UTC), Decimal("110"), Decimal("110")),
    ]
    raw = run_active_backtest(
        candles,
        InvestmentPlan("100", "40", "0", 15),
        start,
        end,
        lambda wallet: (
            StrategyDecision(Action.BUY, wallet.cash)
            if wallet.timestamp == start
            else StrategyDecision()
        ),
    )
    raw.update(
        {
            "execution_scope": {
                "selected_pair": "BTC/EUR",
                "config_digest": "fixture",
                "timeframe": "4h",
                "generated_config": "fixture.json",
            },
            "strategy": "RoundupBreakoutStrategy",
            "pair": "BTC/EUR",
            "investment_plan": {
                "initial_capital": Decimal("100"),
                "monthly_budget": Decimal("40"),
                "fee_ratio": Decimal("0"),
                "contribution_day": 15,
            },
        }
    )
    artifact = build_active_result(
        raw,
        strategy="RoundupBreakoutStrategy",
        pair="BTC/EUR",
        timeframe="4h",
        timerange="20260101-20260201",
        execution_model="fixture-v1",
        effective_settings={},
    )
    # JSON round-trip proves the machine-readable Decimal representation is accepted.
    artifact = json.loads(json.dumps(artifact, default=str))
    validate_active_result(
        artifact,
        strategy="RoundupBreakoutStrategy",
        pair="BTC/EUR",
        capital_mode="recurring_monthly_contributions",
    )
    artifact["trade_ledger"][0]["cash_available"] = "1"
    with pytest.raises(ValueError, match="buy exceeds"):
        validate_active_result(artifact)


def test_end_to_end_fixture_generates_validates_combines_and_summarizes(
    tmp_path, monkeypatch
) -> None:
    from roundup_crypto_lab.active_comparison import main
    from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER

    start = datetime(2026, 1, 1, tzinfo=UTC)
    raw = run_active_backtest(
        [Candle(start, Decimal("100"), Decimal("100"))],
        InvestmentPlan("100", "40", "0", 1),
        start,
        datetime(2026, 1, 2, tzinfo=UTC),
        lambda _: StrategyDecision(),
    )
    raw["execution_scope"] = {
        "selected_pair": "BTC/EUR",
        "config_digest": "fixture",
        "timeframe": "4h",
        "generated_config": "fixture.json",
    }
    raw["investment_plan"] = {
        "initial_capital": Decimal("100"),
        "monthly_budget": Decimal("40"),
        "fee_ratio": Decimal("0"),
        "contribution_day": 1,
    }
    results = []
    for strategy in STRATEGY_ORDER:
        result = build_active_result(
            raw,
            strategy=strategy,
            pair="BTC/EUR",
            timeframe="4h",
            timerange="20260101-20260102",
            execution_model="fixture-v1",
            effective_settings={},
        )
        path = tmp_path / f"{strategy}.json"
        path.write_text(json.dumps(result, default=str))
        results.append(path)
    native = [
        {
            "strategy": name,
            "trades": 0,
            "profit_total": 0.0,
            "profit_total_abs": 0.0,
            "winrate": 0.0,
            "max_drawdown_account": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
        }
        for name in STRATEGY_ORDER
    ]
    native_path, metadata_path = tmp_path / "native.json", tmp_path / "metadata.json"
    native_path.write_text(json.dumps(native))
    metadata_path.write_text(
        json.dumps(
            {
                "timerange": "20260101-20260102",
                "timeframe": "4h",
                "pairs": "BTC/EUR",
                "commit_sha": "fixture",
                "freqtrade_version": "fixture",
                "python_version": "fixture",
                "run_date_utc": "fixture",
            }
        )
    )
    output, summary = tmp_path / "combined.json", tmp_path / "summary.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "active_comparison",
            "--native-comparison",
            str(native_path),
            "--metadata",
            str(metadata_path),
            "--output",
            str(output),
            "--summary",
            str(summary),
            *sum((["--active-result", str(path)] for path in results), []),
        ],
    )
    main()
    assert len(json.loads(output.read_text())["active_investor_cash_flow_simulation"]) == 7
    assert "Native Freqtrade one-shot reference" in summary.read_text()
    assert "Active investor cash-flow simulation" in summary.read_text()
