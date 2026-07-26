from datetime import UTC, datetime

import pytest

from roundup_crypto_lab.rolling_keradx_dca import add_months, generate_windows


def test_add_months_clamps_end_of_month() -> None:
    value = datetime(2024, 1, 31, tzinfo=UTC)
    assert add_months(value, 1) == datetime(2024, 2, 29, tzinfo=UTC)


def test_generate_windows_keeps_only_complete_windows() -> None:
    windows = generate_windows(
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2021, 1, 1, tzinfo=UTC),
        window_months=6,
        step_months=3,
    )
    assert [window["timerange"] for window in windows] == [
        "20200101-20200701",
        "20200401-20201001",
        "20200701-20210101",
    ]


def test_generate_windows_rejects_campaign_without_complete_window() -> None:
    with pytest.raises(ValueError, match="complete rolling window"):
        generate_windows(
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2020, 6, 1, tzinfo=UTC),
            window_months=12,
            step_months=1,
        )


def test_generate_windows_rejects_non_positive_step() -> None:
    with pytest.raises(ValueError, match="positive"):
        generate_windows(
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2021, 1, 1, tzinfo=UTC),
            window_months=6,
            step_months=0,
        )
