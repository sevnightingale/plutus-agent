"""Shared file safety rules used by both tools and ACP shims."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _hermes_home_path() -> Path:
    """Resolve the active HERMES_HOME (profile-aware) without circular imports."""
    try:
        from plutus_constants import get_hermes_home  # local import to avoid cycles
        return get_hermes_home()
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def build_write_denied_paths(home: str) -> set[str]:
    """Return exact sensitive paths that must never be written."""
    hermes_home = _hermes_home_path()
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".ssh", "authorized_keys"),
            os.path.join(home, ".ssh", "id_rsa"),
            os.path.join(home, ".ssh", "id_ed25519"),
            os.path.join(home, ".ssh", "config"),
            str(hermes_home / ".env"),
            os.path.join(home, ".bashrc"),
            os.path.join(home, ".zshrc"),
            os.path.join(home, ".profile"),
            os.path.join(home, ".bash_profile"),
            os.path.join(home, ".zprofile"),
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }


def build_write_denied_prefixes(home: str) -> list[str]:
    """Return sensitive directory prefixes that must never be written."""
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            "/etc/sudoers.d",
            "/etc/systemd",
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
        ]
    ]


def get_safe_write_roots() -> list[str]:
    """Return resolved HERMES_WRITE_SAFE_ROOT entries.

    Multiple roots may be specified os.pathsep-separated (``:`` on Unix,
    ``;`` on Windows), e.g. ``/home/user/repo:/home/user/.plutus-agent``.
    A write is allowed if it falls under any of them.
    """
    raw = os.getenv("PLUTUS_WRITE_SAFE_ROOT") or os.getenv("HERMES_WRITE_SAFE_ROOT", "")
    if not raw:
        return []
    roots: list[str] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(os.path.realpath(os.path.expanduser(part)))
        except Exception:
            continue
    return roots


def get_safe_write_root() -> Optional[str]:
    """Return the first configured HERMES_WRITE_SAFE_ROOT entry (back-compat)."""
    roots = get_safe_write_roots()
    return roots[0] if roots else None


def is_write_denied(path: str) -> bool:
    """Return True if path is blocked by the write denylist or safe root."""
    home = os.path.realpath(os.path.expanduser("~"))
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    if resolved in build_write_denied_paths(home):
        return True
    for prefix in build_write_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    safe_roots = get_safe_write_roots()
    if safe_roots:
        inside = any(
            resolved == root or resolved.startswith(root + os.sep)
            for root in safe_roots
        )
        if not inside:
            return True

    return False


def get_safe_read_roots() -> list[str]:
    """Return resolved HERMES_READ_SAFE_ROOT entries (multi-root, os.pathsep-separated).

    Falls back to ``HERMES_WRITE_SAFE_ROOT`` when ``HERMES_READ_SAFE_ROOT`` is
    unset, so the common case ("agent's territory = readable + writeable area")
    needs only one env var.  Set ``HERMES_READ_SAFE_ROOT`` explicitly only when
    reads and writes need different scopes.
    """
    raw = os.getenv("PLUTUS_READ_SAFE_ROOT") or os.getenv("HERMES_READ_SAFE_ROOT", "")
    if not raw:
        return get_safe_write_roots()
    roots: list[str] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(os.path.realpath(os.path.expanduser(part)))
        except Exception:
            continue
    return roots


def get_read_block_error(path: str) -> Optional[str]:
    """Return an error message when a read is blocked, or None when allowed.

    Blocks:
      1. Internal Hermes cache files (anti-prompt-injection — pre-existing).
      2. Paths outside the configured read roots (agent territory enforcement).
    """
    resolved = Path(path).expanduser().resolve()
    hermes_home = _hermes_home_path().resolve()
    blocked_dirs = [
        hermes_home / "skills" / ".hub" / "index-cache",
        hermes_home / "skills" / ".hub",
    ]
    for blocked in blocked_dirs:
        try:
            resolved.relative_to(blocked)
        except ValueError:
            continue
        return (
            f"Access denied: {path} is an internal Hermes cache file "
            "and cannot be read directly to prevent prompt injection. "
            "Use the skills_list or skill_view tools instead."
        )

    safe_roots = get_safe_read_roots()
    if safe_roots:
        resolved_str = str(resolved)
        inside = any(
            resolved_str == root or resolved_str.startswith(root + os.sep)
            for root in safe_roots
        )
        if not inside:
            return (
                f"Access denied: {path} is outside the agent's filesystem "
                f"territory. Allowed roots: {os.pathsep.join(safe_roots)}. "
                "Reads outside your territory are blocked at the harness level."
            )

    return None
