"""Read dgclaw-skill's .env and persist relevant keys into ~/.plutus-agent/.env."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from trading.integrations.acp._env import set_env, get_env  # reuse atomic dotenv writer

from . import _cli

logger = logging.getLogger(__name__)


def dgclaw_dotenv_path() -> Path:
    return _cli.get_root() / ".env"


def read_dgclaw_env(key: str) -> Optional[str]:
    """Return ``key``'s value from the dgclaw-skill repo's .env."""
    p = dgclaw_dotenv_path()
    if not p.exists():
        return None
    try:
        from dotenv import dotenv_values
        return dotenv_values(str(p)).get(key)
    except ImportError:
        # Manual parse fallback
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("'\"")
    return None


def persist_from_dgclaw_dotenv(*keys: str) -> List[str]:
    """For each key, read from dgclaw-skill .env and write to ~/.plutus-agent/.env.

    Returns a list of keys that were successfully copied.
    """
    persisted: List[str] = []
    for k in keys:
        value = read_dgclaw_env(k)
        if value is None:
            logger.warning("key %s missing from dgclaw-skill .env at %s",
                           k, dgclaw_dotenv_path())
            continue
        set_env(k, value)
        persisted.append(k)
    return persisted
