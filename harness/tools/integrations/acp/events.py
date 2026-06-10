"""ACP events — subscribes to `acp events listen` background stream.

Currently a no-op at module import: the stream-start is on-demand (called
from `start_event_stream()` if/when needed). v1 doesn't auto-start it
because the watcher daemon's HL alerts already provide the needed wake
events; ACP-specific wake events (job completion, etc.) become a future
enhancement.

Reserves the path so future Plutus self-modification can fill in the
stream-start logic without disturbing the rest of the integration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import _cli

logger = logging.getLogger(__name__)


def start_event_stream(output_file: Optional[str] = None) -> Dict[str, Any]:
    """Spawn `acp events listen --output <file>` as a background subprocess.

    If ``output_file`` is None, defaults to ~/.plutus-agent/wake_events.ndjson
    so ACP events land in the same sink as the watcher daemon's events.

    Returns the spawned process metadata dict, or an empty dict if ACP isn't
    installed.
    """
    if not _cli.is_installed():
        logger.debug("acp not installed; cannot start event stream")
        return {}

    if output_file is None:
        from plutus_constants import get_hermes_home
        output_file = str(get_hermes_home() / "wake_events.ndjson")

    try:
        return _cli.acp_async_spawn("events", "listen",
                                    "--output", output_file)
    except Exception as exc:
        logger.warning("acp events listen failed to spawn: %s", exc)
        return {"error": str(exc)}
