from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise

from roundup_crypto_lab.passive_benchmarks import _assert_accounting_invariants


def test_cash_invariant_scales_with_long_daily_dca_rounding_residue() -> None:
    """Reproduce the 20260125-20260723 DailyDCA residue from the failed workflow."""
    boundaries = [
        datetime(2026, 1, 25, tzinfo=UTC),
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 3, 1, tzinfo=UTC),
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 23, tzinfo=UTC),
    ]
    fee_ratio = Decimal("0.0026")
    execution_price = final_price = Decimal("100")
    purchases = []
    running_quantity = running_fees = Decimal("0")

    for start, end in pairwise(boundaries):
        count = (end - start).days
        portion = Decimal("40") / count
        amounts = [portion] * (count - 1)
        amounts.append(Decimal("40") - sum(amounts, Decimal("0")))
        for gross in amounts:
            fee = gross * fee_ratio
            net = gross * (Decimal("1") - fee_ratio)
            acquired = net / execution_price
            running_quantity += acquired
            running_fees += fee
            purchases.append(
                {
                    "gross_contribution": gross,
                    "fee_paid": fee,
                    "net_contribution": net,
                    "execution_price": execution_price,
                    "quantity": acquired,
                    "cumulative_quantity": running_quantity,
                    "cumulative_fees": running_fees,
                }
            )

    contributions = Decimal("280")
    invested = sum((purchase["gross_contribution"] for purchase in purchases), Decimal("0"))
    residue = abs(contributions - invested)

    assert len(purchases) == 179
    assert residue == Decimal("3.9e-24")
    assert residue > Decimal("1e-24")
    assert residue <= Decimal("1e-24") * len(purchases)

    _assert_accounting_invariants(
        purchases,
        quantity=running_quantity,
        cash=Decimal("0"),
        contributions=contributions,
        invested=invested,
        fees=running_fees,
        final_price=final_price,
        final_value=running_quantity * final_price,
        expected_contributions=contributions,
    )
