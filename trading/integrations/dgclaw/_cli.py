"""Subprocess wrappers around the dgclaw-skill bash + tsx entry points.

dgclaw-skill is cloned to ``$DGCLAW_SKILL_ROOT`` (default ``~/dgclaw-skill``).
Three wrappers:
- ``dgclaw(*args)`` runs ``bash $ROOT/scripts/dgclaw.sh <args>``
- ``dgclaw_trade(*args)`` runs ``npx tsx $ROOT/scripts/trade.ts <args>``
- ``dgclaw_script(name, *args)`` runs ``npx tsx $ROOT/scripts/<name> <args>``
  for activate-unified.ts / add-api-wallet.ts / withdraw.ts.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


DEFAULT_ROOT = "~/dgclaw-skill"


class DgclawCLIError(RuntimeError):
    """Raised when dgclaw-skill is missing, fails, or returns unparseable output."""


def get_root() -> Path:
    root = os.getenv("DGCLAW_SKILL_ROOT") or DEFAULT_ROOT
    return Path(root).expanduser()


def is_installed() -> bool:
    root = get_root()
    return root.is_dir() and (root / "node_modules").is_dir()


def _try_parse_json(stdout: str) -> Any:
    if not stdout.strip():
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"raw_output": stdout}


def _run(cmd: list, cwd: Optional[str] = None, timeout: int = 600) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, check=False,
            timeout=timeout, cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise DgclawCLIError(f"binary not found: {exc.filename}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DgclawCLIError(f"command timed out after {timeout}s: {' '.join(cmd)}") from exc

    if proc.returncode != 0:
        raise DgclawCLIError(
            f"{' '.join(cmd[:3])} exited with code {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or 'no output').strip()}"
        )
    return proc.stdout or ""


def dgclaw(*args: str, capture: bool = True, timeout: int = 600) -> Any:
    """Run ``bash $ROOT/scripts/dgclaw.sh <args>``."""
    if not is_installed():
        raise DgclawCLIError(
            f"dgclaw-skill not installed at {get_root()}. Run "
            "dgclaw_install() to clone + npm install."
        )
    script = get_root() / "scripts" / "dgclaw.sh"
    if not script.is_file():
        raise DgclawCLIError(f"dgclaw.sh not found at {script}")
    out = _run(["bash", str(script), *args], cwd=str(get_root()), timeout=timeout)
    return _try_parse_json(out) if capture else out


def dgclaw_trade(*args: str, capture: bool = True, timeout: int = 300) -> Any:
    """Run ``npx tsx $ROOT/scripts/trade.ts <args>``."""
    if not is_installed():
        raise DgclawCLIError(
            f"dgclaw-skill not installed at {get_root()}."
        )
    script = get_root() / "scripts" / "trade.ts"
    out = _run(
        ["npx", "tsx", str(script), *args],
        cwd=str(get_root()), timeout=timeout,
    )
    return _try_parse_json(out) if capture else out


def dgclaw_script(name: str, *args: str, capture: bool = True, timeout: int = 600) -> Any:
    """Run ``npx tsx $ROOT/scripts/<name> <args>``."""
    if not is_installed():
        raise DgclawCLIError(
            f"dgclaw-skill not installed at {get_root()}."
        )
    script = get_root() / "scripts" / name
    if not script.is_file():
        raise DgclawCLIError(f"{name} not found at {script}")
    out = _run(
        ["npx", "tsx", str(script), *args],
        cwd=str(get_root()), timeout=timeout,
    )
    return _try_parse_json(out) if capture else out
