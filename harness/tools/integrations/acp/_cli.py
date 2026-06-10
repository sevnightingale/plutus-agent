"""Subprocess wrapper around the ``acp`` CLI binary.

Not registered as a tool — used internally by the setup / data point /
operations / identity modules. Designed for testability: tests
``monkeypatch.setattr(_cli, "acp", fake_runner)`` to short-circuit
real subprocess calls.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class ACPCLIError(RuntimeError):
    """Raised when the acp CLI is missing, fails, or returns unparseable output."""


def is_installed() -> bool:
    """Return True when the ``acp`` binary is on PATH."""
    return shutil.which("acp") is not None


def acp(*args: str,
        capture: bool = True,
        timeout: int = 300,
        cwd: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
        json_flag: bool = True) -> Union[Dict[str, Any], str]:
    """Run ``acp <args> [--json]`` and return parsed JSON (or stdout text).

    ``capture=False`` returns raw stdout (used for the interactive
    flows like ``acp configure`` whose stdout includes the OAuth URL
    rather than structured JSON).
    """
    if not is_installed():
        raise ACPCLIError(
            "acp CLI is not on PATH. Install via "
            "`npm install -g @virtuals-protocol/acp-cli` or call "
            "acp_install() to do it for you."
        )

    cmd = ["acp", *args]
    if json_flag:
        cmd.append("--json")

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ACPCLIError(f"acp {' '.join(args)} timed out after {timeout}s") from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        raise ACPCLIError(
            f"acp {' '.join(args)} exited with code {proc.returncode}: "
            f"{stderr.strip() or stdout.strip() or 'no output'}"
        )

    if not capture:
        return stdout

    if not stdout.strip():
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Some commands may emit non-JSON even with --json. Surface raw
        # text rather than crashing.
        return {"raw_output": stdout}


def acp_async_spawn(*args: str,
                    output_file: Optional[str] = None,
                    extra_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Spawn an ``acp`` subprocess that runs in the background.

    Used for ``acp configure`` / ``acp agent add-signer`` (interactive,
    waits on browser approval) and ``acp events listen`` (long-running
    NDJSON stream). Delegates to ``tools.process_registry.spawn_local``.
    """
    from tools.process_registry import process_registry

    cmd = "acp " + " ".join(args)
    if output_file:
        cmd += f" > {output_file}"

    session = process_registry.spawn_local(
        command=cmd,
        env_vars=extra_env,
    )
    return {
        "session_id": session.id,
        "command": cmd,
        "status": "spawned",
    }
