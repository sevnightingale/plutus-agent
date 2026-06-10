"""Shared helpers for the registry-backed dispatcher tools.

Kept out of ``tools/dispatchers/__init__.py`` so the package init stays
empty, and out of any individual dispatcher so they read flat.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def session_id_from_context() -> Optional[str]:
    """Best-effort session id pulled from the gateway's contextvars.

    Returns None outside of gateway-routed sessions (CLI, tests, scripts).
    Stored as ``session_id`` on lifecycle.db rows; nullable.
    """
    try:
        from harness.gateway.session_context import get_session_env
    except Exception:
        return None
    return (get_session_env("HERMES_SESSION_KEY") or None) \
        or (get_session_env("HERMES_GATEWAY_SESSION") or None) \
        or None


def json_dumps_compact(obj: Any) -> str:
    """JSON-encode with compact separators for storage in *_json columns."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)
