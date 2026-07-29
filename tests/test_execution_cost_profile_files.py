from pathlib import Path

from roundup_crypto_lab.execution_costs import load_cost_profile


PROFILE_DIR = Path("config/execution-cost-profiles")


def test_committed_execution_cost_profiles_are_valid_and_distinct() -> None:
    paths = sorted(PROFILE_DIR.glob("*.json"))
    profiles = [load_cost_profile(path) for path in paths]
    assert [profile.cost_profile_id for profile in profiles] == [
        "frictionless-control-v1",
        "hypothetical-fixed-cost-v1",
        "proportional-fee-v1",
        "proportional-plus-spread-v1",
    ]
    assert len({profile.digest for profile in profiles}) == len(profiles)

    fixed = next(
        profile
        for profile in profiles
        if profile.cost_profile_id == "hypothetical-fixed-cost-v1"
    )
    assert fixed.profile_kind == "sensitivity"
    assert fixed.fixed_order_fee > 0
    assert "does not represent Kraken" in fixed.description
