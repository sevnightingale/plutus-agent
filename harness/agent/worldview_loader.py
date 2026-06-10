"""Load WORLDVIEW.md (PLUTUS Stratum 1: cross-session bridge).

Mirrors ``agent.prompt_builder.load_soul_md``: read the file from
HERMES_HOME, run through scan + truncate defenders, return content
(or ``None`` if absent / empty).

Frozen-snapshot semantics: this loader is called ONCE per session
during prompt assembly. Plutus's writes to ``WORLDVIEW.md`` during
the session take effect on the next session — the prompt is cached
so we cannot mutate it mid-conversation. The discipline lives in the
``worldview-discipline`` skill.
"""

from __future__ import annotations

import logging
from typing import Optional

from harness.agent.prompt_builder import _scan_context_content, _truncate_content
from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)


def load_worldview_md() -> Optional[str]:
    """Load WORLDVIEW.md from HERMES_HOME and return its content, or None.

    Returns the truncated/scanned content; returns ``None`` when the file
    is absent, empty, or unreadable. ``run_agent.py`` injects the result
    into the system prompt right after SOUL.md (identity-adjacent slot).
    """
    try:
        from harness.cli.config import ensure_hermes_home
        ensure_hermes_home()
    except Exception as exc:
        logger.debug("Could not ensure HERMES_HOME before loading WORLDVIEW.md: %s", exc)

    wv_path = get_hermes_home() / "WORLDVIEW.md"
    if not wv_path.exists():
        return None
    try:
        content = wv_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        content = _scan_context_content(content, "WORLDVIEW.md")
        content = _truncate_content(content, "WORLDVIEW.md")
        return content
    except Exception as exc:
        logger.debug("Could not read WORLDVIEW.md from %s: %s", wv_path, exc)
        return None
