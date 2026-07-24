from roundup_crypto_lab.unified_comparison import _scenario


def test_numeric_json_and_exact_strings_share_scenario_identity() -> None:
    active = _scenario(
        pair="ETH/EUR",
        timeframe="4h",
        timerange="20260125-20260723",
        plan={
            "initial_capital": "40",
            "monthly_budget": "40",
            "contribution_day": 1,
            "fee_ratio": "0.0026",
        },
        schedule=[
            {
                "contributed_at": "2026-01-25T00:00:00+00:00",
                "amount": "40",
                "kind": "initial",
            },
            {
                "contributed_at": "2026-02-01T00:00:00+00:00",
                "amount": "40",
                "kind": "monthly",
            },
        ],
        capital_mode="recurring_monthly_contributions",
        repository_commit="abc123",
    )
    passive = _scenario(
        pair="ETH/EUR",
        timeframe="4h",
        timerange="20260125-20260723",
        plan={
            "initial_capital": 40.0,
            "monthly_budget": 40.0,
            "contribution_day": 1,
            "fee_ratio": 0.0026,
        },
        schedule=[
            {
                "contributed_at": "2026-01-25T00:00:00+00:00",
                "amount": 40.0,
                "kind": "initial",
            },
            {
                "contributed_at": "2026-02-01T00:00:00+00:00",
                "amount": 40.0,
                "kind": "monthly",
            },
        ],
        capital_mode="recurring_monthly_contributions",
        repository_commit="abc123",
    )

    assert active == passive
    assert active["initial_capital"] == "40"
    assert active["monthly_budget"] == "40"
    assert active["fee_ratio"] == "0.0026"
    assert active["contribution_schedule"][0]["amount"] == "40"
