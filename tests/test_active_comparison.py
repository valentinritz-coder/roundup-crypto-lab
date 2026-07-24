import copy
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.active_backtests import (
    Action,
    Candle,
    CapitalMode,
    StrategyDecision,
    run_active_backtest,
)
from roundup_crypto_lab.active_comparison import (
    build_active_result,
    main,
    validate_active_result,
)
from roundup_crypto_lab.all_strategy_comparison import STRATEGY_ORDER
from roundup_crypto_lab.investment_plan import InvestmentPlan

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 3, 1, tzinfo=UTC)


def _raw_result(mode: CapitalMode = CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS):
    candles = [
        Candle(START, Decimal("100"), Decimal("102"), Decimal("103"), Decimal("99")),
        Candle(datetime(2026, 1, 10, tzinfo=UTC), Decimal("110"), Decimal("110")),
        Candle(datetime(2026, 1, 15, tzinfo=UTC), Decimal("100"), Decimal("100")),
        Candle(
            datetime(2026, 1, 16, tzinfo=UTC),
            Decimal("100"),
            Decimal("85"),
            Decimal("101"),
            Decimal("80"),
        ),
        Candle(datetime(2026, 2, 15, tzinfo=UTC), Decimal("95"), Decimal("95")),
    ]
    decisions = {
        START: StrategyDecision(Action.BUY, Decimal("90")),
        datetime(2026, 1, 10, tzinfo=UTC): StrategyDecision(
            Action.SELL, exit_tag="close_below_sma20"
        ),
        datetime(2026, 1, 15, tzinfo=UTC): StrategyDecision(Action.BUY, Decimal("40")),
    }
    raw = run_active_backtest(
        candles,
        InvestmentPlan("100", "40", "0.01", 15),
        START,
        END,
        lambda wallet: decisions.get(wallet.timestamp, StrategyDecision()),
        mode=mode,
    )
    raw["execution_scope"] = {
        "selected_pair": "BTC/EUR",
        "config_digest": "fixture-digest",
        "timeframe": "4h",
        "generated_config": "fixture.json",
    }
    raw["investment_plan"] = {
        "initial_capital": Decimal("100"),
        "monthly_budget": Decimal("40"),
        "fee_ratio": Decimal("0.01"),
        "contribution_day": 15,
    }
    return raw


def _artifact(
    strategy: str = STRATEGY_ORDER[0],
    mode: CapitalMode = CapitalMode.RECURRING_MONTHLY_CONTRIBUTIONS,
):
    raw = _raw_result(mode)
    result = build_active_result(
        raw,
        strategy=strategy,
        pair="BTC/EUR",
        timeframe="4h",
        timerange="20260101-20260301",
        execution_model="fixture-v1",
        effective_settings={
            "fee_ratio": "0.01",
            "tradable_balance_ratio": "1",
            "stake_amount": "unlimited",
            "stoploss": "-0.12",
        },
    )
    return json.loads(json.dumps(result, default=str))


def test_meaningful_fixture_contains_contributions_signal_exit_and_stop() -> None:
    artifact = _artifact()
    validate_active_result(artifact)
    assert len(artifact["contribution_ledger"]) == 3
    assert [row["exit_reason"] for row in artifact["trade_ledger"]] == [
        "exit_signal",
        "stop_loss",
    ]
    assert artifact["adapter_metrics"]["entry_count"] == 2
    assert artifact["adapter_metrics"]["exit_count"] == 2


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["contribution_ledger"][0].__setitem__(
                "credited_at", "2026-03-01T00:00:00+00:00"
            ),
            "credited contribution timestamp",
        ),
        (
            lambda value: value["contribution_ledger"][1].__setitem__("kind", "initial"),
            "differs from schedule",
        ),
        (
            lambda value: value["contribution_ledger"][0].__setitem__("amount", "-1"),
            "credited amount must be positive",
        ),
        (
            lambda value: value["trade_ledger"][0].__setitem__("entry_fee", "-1"),
            "entry fee must be non-negative",
        ),
        (
            lambda value: value["trade_ledger"][0].__setitem__("entry_gross_stake", "0"),
            "entry stake must be positive",
        ),
        (
            lambda value: value["trade_ledger"][0].__setitem__("quantity", "0"),
            "quantity must be positive",
        ),
        (
            lambda value: value["trade_ledger"][0].__setitem__("exit_price", "0"),
            "exit price must be positive",
        ),
        (
            lambda value: value["equity_curve"][1].__setitem__("equity", "999"),
            "equity row identity",
        ),
        (
            lambda value: value["equity_curve"][-1].__setitem__("crypto_value", "1"),
            "equity row identity",
        ),
        (
            lambda value: value["adapter_metrics"].__setitem__("fees_paid", "0"),
            "fees paid differ",
        ),
    ],
)
def test_validator_rejects_adversarial_mutations(mutate, message: str) -> None:
    artifact = _artifact()
    mutate(artifact)
    with pytest.raises(ValueError, match=message):
        validate_active_result(artifact)


def test_open_trade_must_be_last() -> None:
    artifact = _artifact()
    first = artifact["trade_ledger"][0]
    first.update(
        {
            "exit_timestamp": None,
            "exit_price": None,
            "exit_fee": None,
            "exit_reason": None,
            "net_proceeds": None,
            "total_fees": None,
        }
    )
    with pytest.raises(ValueError, match="open trade must be the final"):
        validate_active_result(artifact)


def test_all_registered_results_are_combined_and_summarized(tmp_path, monkeypatch) -> None:
    active_paths = []
    for strategy in STRATEGY_ORDER:
        path = tmp_path / f"{strategy}.json"
        path.write_text(json.dumps(_artifact(strategy), indent=2), encoding="utf-8")
        active_paths.append(path)
    native_path = tmp_path / "native.json"
    native_path.write_text(
        json.dumps(
            [
                {
                    "strategy": strategy,
                    "trades": 2,
                    "profit_total": 0.01,
                    "profit_total_abs": 1.0,
                    "winrate": 0.5,
                    "max_drawdown_account": 0.1,
                    "profit_factor": 1.1,
                    "expectancy": 0.5,
                }
                for strategy in STRATEGY_ORDER
            ]
        ),
        encoding="utf-8",
    )
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "timerange": "20260101-20260301",
                "timeframe": "4h",
                "pairs": "BTC/EUR",
                "config": "fixture.json",
                "config_digest": "fixture-digest",
                "fee": "0.01",
                "starting_balance": "100",
                "strategies": list(STRATEGY_ORDER),
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "controlled.json"
    summary = tmp_path / "summary.md"
    argv = [
        "active_comparison",
        "--native-comparison",
        str(native_path),
        "--metadata",
        str(metadata_path),
        "--output",
        str(output),
        "--summary",
        str(summary),
    ]
    for path in active_paths:
        argv.extend(["--active-result", str(path)])
    monkeypatch.setattr("sys.argv", argv)
    main()
    controlled = json.loads(output.read_text(encoding="utf-8"))
    assert controlled["schema_version"] == "controlled-comparison/v1"
    assert len(controlled["active_investor_cash_flow_simulation"]) == len(STRATEGY_ORDER)
    text = summary.read_text(encoding="utf-8")
    assert "# Native Freqtrade one-shot reference" in text
    assert "# Active investor cash-flow simulation" in text
    assert "Native one-shot profit is not used to rank" in text


def test_cross_result_experiment_mismatch_is_rejected() -> None:
    results = [_artifact(strategy) for strategy in STRATEGY_ORDER]
    broken = copy.deepcopy(results[-1])
    broken["experiment"]["selected_pair"] = "ETH/EUR"
    from roundup_crypto_lab.active_comparison import identity

    broken["experiment"]["execution_scope"]["selected_pair"] = "ETH/EUR"
    broken["experiment"]["experiment_id"] = identity(broken["experiment"])
    results[-1] = broken
    from roundup_crypto_lab.active_comparison import validate_result_set

    with pytest.raises(ValueError, match="do not share one experiment"):
        validate_result_set(results)
