"""Read/write helpers for ~/.plutus-agent/.env from the ACP integration.

ACP setup writes important values (ACP_AGENT_WALLET from acp_whoami,
DGCLAW_API_KEY from dgclaw_join, HL_API_WALLET_KEY from
add-api-wallet.ts) into ~/.plutus-agent/.env. We use python-dotenv's
set_key for atomic file edits so the operator's existing values stay
intact.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)


def env_path() -> Path:
    return get_hermes_home() / ".env"


def set_env(key: str, value: str) -> None:
    """Atomic upsert of ``key=value`` in ~/.plutus-agent/.env."""
    try:
        from dotenv import set_key
    except ImportError as exc:
        raise RuntimeError(
            "python-dotenv not installed; cannot persist env vars"
        ) from exc

    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    set_key(str(path), key, value, quote_mode="never")
    # Update the live process env as well so subsequent calls in this
    # session see the new value (the next gateway/watcher restart picks
    # it up from .env on its own).
    os.environ[key] = value
    logger.info("persisted %s into %s", key, path)


def get_env(key: str) -> Optional[str]:
    """Read a key from ~/.plutus-agent/.env (file-backed truth)."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return os.getenv(key)
    path = env_path()
    if not path.exists():
        return os.getenv(key)
    values = dotenv_values(str(path))
    return values.get(key) or os.getenv(key)
