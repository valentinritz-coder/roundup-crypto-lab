from pathlib import Path

import pytest

from roundup_crypto_lab.roundups import (
    RoundupInputError,
    calculate_roundup_cents,
    decimal_euros_to_cents,
    load_roundups,
)


def test_calculate_roundup_cents() -> None:
    assert calculate_roundup_cents(-432) == 68
    assert calculate_roundup_cents(-6317) == 83
    assert calculate_roundup_cents(-400) == 0
    assert calculate_roundup_cents(1250) == 0
    assert calculate_roundup_cents(0) == 0


def test_decimal_parser_is_exact() -> None:
    assert decimal_euros_to_cents("-4.32") == -432
    assert decimal_euros_to_cents("-4,32") == -432
    assert decimal_euros_to_cents("0.005") == 1


def test_load_roundups_example() -> None:
    path = Path("data/examples/transactions.csv")
    records = load_roundups(path)
    assert len(records) == 5
    assert sum(record.roundup_cents for record in records) == 151


def test_duplicate_ids_fail_loudly(tmp_path: Path) -> None:
    csv_path = tmp_path / "duplicate.csv"
    csv_path.write_text(
        "date,description,amount,transaction_id\n"
        "2026-07-01,Cafe,-2.40,same\n"
        "2026-07-01,Cafe,-2.40,same\n",
        encoding="utf-8",
    )
    with pytest.raises(RoundupInputError, match="duplicate transaction_id"):
        load_roundups(csv_path)
