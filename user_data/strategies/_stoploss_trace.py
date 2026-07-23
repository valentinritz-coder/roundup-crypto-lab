"""Optional JSONL tracing for native Freqtrade custom_stoploss callbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def trace_custom_stoploss(record: dict[str, Any]) -> None:
    """Append one callback record when FREQTRADE_STOP_TRACE_PATH is configured."""
    destination = os.environ.get("FREQTRADE_STOP_TRACE_PATH")
    if not destination:
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, default=str) + "\n")
