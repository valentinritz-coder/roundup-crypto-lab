"""Optional JSONL tracing for native Freqtrade custom-stop callbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def trace_custom_stoploss(**record: Any) -> None:
    """Append one callback record when ROUNDUP_STOPLOSS_TRACE is configured."""
    destination = os.environ.get("ROUNDUP_STOPLOSS_TRACE")
    if not destination:
        return

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
