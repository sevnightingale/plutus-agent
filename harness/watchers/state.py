"""Watcher daemon state — JSON file with per-alert state + throttle bookkeeping.

State shape:
    {
      "alerts": {
        "<alert_name>": {
          "state": <opaque alert-defined dict>,
          "last_fired_ts": <epoch seconds, optional>
        },
        ...
      }
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)


_LOCK = threading.Lock()


def state_path() -> Path:
    return get_hermes_home() / "watcher_state.json"


def load_state() -> Dict[str, Any]:
    p = state_path()
    if not p.exists():
        return {"alerts": {}}
    try:
        with _LOCK:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("watcher_state corrupt at %s: %s — resetting", p, exc)
        return {"alerts": {}}


def save_state(state: Dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, p)


def get_alert_state(alert_name: str) -> Optional[Dict[str, Any]]:
    s = load_state()
    return s.get("alerts", {}).get(alert_name, {}).get("state")


def update_alert_state(
    alert_name: str,
    new_state: Dict[str, Any],
    fired_ts: Optional[float] = None,
) -> None:
    s = load_state()
    alerts = s.setdefault("alerts", {})
    entry = alerts.setdefault(alert_name, {})
    entry["state"] = new_state
    if fired_ts is not None:
        entry["last_fired_ts"] = fired_ts
    save_state(s)


def get_last_fired_ts(alert_name: str) -> Optional[float]:
    s = load_state()
    entry = s.get("alerts", {}).get(alert_name, {})
    return entry.get("last_fired_ts")
