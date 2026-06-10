"""Active thesis monitors registry — `~/.plutus-agent/active-thesis-monitors.json`.

V2 Stratum: per architecture-v2 §7. The file holds one entry per open thesis
that needs ongoing monitoring. plutus-ops reads it every 30 min during its
sweep (Flavor A monitoring); plutus-thesis crons (Flavor B) reference an
entry by `thesis_id` for high-cadence per-thesis monitoring.

Write contention:
- plutus-main writes (rarely — Phase 6 of each beat: add new positions,
  remove closed positions)
- plutus-ops reads (every tick — never writes)
- plutus-thesis crons read (per tick — never write)

Single-writer (plutus-main) + multi-reader is the contract. Atomic-rename
write pattern (tempfile + os.replace) handles file-level integrity. No
explicit locking needed.

Schema::

    {
      "version": 1,
      "updated_at": <unix_ts>,
      "monitors": [
        {
          "thesis_id": 9,
          "position_id": 7,
          "symbol": "BTC",
          "side": "long",
          "data_points_to_watch": ["hl_price", "hl_cvd", "ta_rsi"],
          "invalidation_rules": [
            {"rule": "price < 75200", "action": "exit"},
            {"rule": "ta_rsi > 75 AND hl_cvd_z < -1.0", "action": "exit"}
          ],
          "horizon_ts": 1779700000.0,
          "added_at": 1779000000.0,
          "added_by_session_id": "<main session id at open>"
        }
      ]
    }
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


MONITORS_FILE_VERSION = 1


def _hermes_home() -> Path:
    from harness.constants import get_hermes_home
    return Path(get_hermes_home())


def _monitors_path() -> Path:
    return _hermes_home() / "active-thesis-monitors.json"


def _empty_state() -> Dict[str, Any]:
    return {"version": MONITORS_FILE_VERSION, "updated_at": 0.0, "monitors": []}


def _read_state() -> Dict[str, Any]:
    """Internal state read. Returns empty (well-formed) on missing/corrupt."""
    path = _monitors_path()
    if not path.exists():
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict) or "monitors" not in state:
            return _empty_state()
        if not isinstance(state["monitors"], list):
            return _empty_state()
        state.setdefault("version", MONITORS_FILE_VERSION)
        state.setdefault("updated_at", 0.0)
        return state
    except (OSError, json.JSONDecodeError):
        return _empty_state()


def read_active_monitors() -> List[Dict[str, Any]]:
    """Return the list of active monitor entries. Empty list on missing file."""
    return _read_state()["monitors"]


def add_monitor(
    *,
    thesis_id: int,
    position_id: int,
    symbol: str,
    side: str,
    data_points_to_watch: List[str],
    invalidation_rules: List[Dict[str, Any]],
    horizon_ts: float,
    added_by_session_id: Optional[str] = None,
) -> None:
    """Append (or replace by thesis_id) a monitor entry. Atomic write.

    If an entry with the same thesis_id exists, it's REPLACED (not duplicated).
    This is the catch-up path: plutus-main re-adding an entry it already had
    is a no-op + timestamp refresh.
    """
    state = _read_state()
    now = time.time()
    new_entry = {
        "thesis_id": int(thesis_id),
        "position_id": int(position_id),
        "symbol": symbol,
        "side": side,
        "data_points_to_watch": list(data_points_to_watch),
        "invalidation_rules": list(invalidation_rules),
        "horizon_ts": float(horizon_ts),
        "added_at": now,
        "added_by_session_id": added_by_session_id or "",
    }
    monitors = [m for m in state["monitors"] if m.get("thesis_id") != int(thesis_id)]
    monitors.append(new_entry)
    state["monitors"] = monitors
    state["updated_at"] = now
    state["version"] = MONITORS_FILE_VERSION
    _atomic_write_json(_monitors_path(), state)


def remove_monitor(thesis_id: int) -> bool:
    """Remove the entry with the given thesis_id. Returns True if removed."""
    state = _read_state()
    before = len(state["monitors"])
    state["monitors"] = [m for m in state["monitors"] if m.get("thesis_id") != int(thesis_id)]
    removed = len(state["monitors"]) < before
    if removed:
        state["updated_at"] = time.time()
        _atomic_write_json(_monitors_path(), state)
    return removed


def update_monitor(thesis_id: int, **fields: Any) -> bool:
    """Partial update of fields on an existing monitor entry. Returns True if found.

    Useful for plutus-thesis to re-tag horizon_ts, plutus-main to swap
    data_points_to_watch when thesis evolves, etc.
    """
    state = _read_state()
    found = False
    for m in state["monitors"]:
        if m.get("thesis_id") == int(thesis_id):
            for k, v in fields.items():
                m[k] = v
            found = True
            break
    if found:
        state["updated_at"] = time.time()
        _atomic_write_json(_monitors_path(), state)
    return found


def get_monitor(thesis_id: int) -> Optional[Dict[str, Any]]:
    """Return one monitor entry by thesis_id, or None."""
    for m in read_active_monitors():
        if m.get("thesis_id") == int(thesis_id):
            return m
    return None


def clear_monitors() -> None:
    """Delete the monitors file. Used in tests and operator-driven reset."""
    path = _monitors_path()
    if path.exists():
        path.unlink()


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
