"""Shared helpers for the direct lifecycle query tools."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, List


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[dict]:
    """Convert sqlite3.Row results into plain dicts (JSON-safe)."""
    return [dict(r) for r in rows]


def safe_json_loads(value: Any) -> Any:
    """Decode JSON columns; return ``None`` for NULL and original string on error."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
