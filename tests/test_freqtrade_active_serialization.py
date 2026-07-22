import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roundup_crypto_lab.freqtrade_active import _json


def test_json_serializer_supports_nested_datetime_and_decimal() -> None:
    payload = {
        "rows": [
            {
                "timestamp": datetime(2026, 7, 22, 19, 28, 22, tzinfo=UTC),
                "amount": Decimal("40.00"),
            }
        ]
    }

    decoded = json.loads(json.dumps(payload, default=_json))

    assert decoded == {
        "rows": [
            {
                "timestamp": "2026-07-22T19:28:22+00:00",
                "amount": "40.00",
            }
        ]
    }


def test_json_serializer_rejects_unknown_types() -> None:
    with pytest.raises(TypeError, match="cannot serialize object"):
        _json(object())
