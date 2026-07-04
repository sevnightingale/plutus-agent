"""request_desk_restart — the sanctioned path to load self-patched code.

Resident interpreters (the gateway and the watchers daemon) import harness/
and trading/ modules once and cache them for life — a repo patch is NOT live
until both restart (2026-07-03: five aborted fills ran on a stale venue.py
while the verified fix sat on disk). This tool does the full sequence the
operator would: queue a resume wake, recycle the watchers daemon, then ask
the gateway for its drain-aware self-restart (finish in-flight turns, exit
75, pm2 revives both processes with fresh imports).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess

from harness.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "request_desk_restart",
    "description": (
        "Gracefully restart the desk's resident processes (gateway + "
        "watchers) so self-patched harness/ or trading/ code on disk becomes "
        "live. The gateway drains in-flight turns first (this turn completes "
        "and reports back before the restart lands); pm2 revives both "
        "processes; a queued wake fires after boot so you resume where you "
        "left off. Use AFTER verifying a repo patch with tests — a patch is "
        "NOT live until this runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "What patch needs loading — goes into the "
                               "resume wake and the logs.",
            },
        },
        "required": ["reason"],
    },
}


def _request_restart(args):
    reason = str(args.get("reason") or "").strip()
    if not reason:
        return tool_error("reason is required — say what patch needs loading")

    # Only the gateway installs a SIGUSR1 restart handler. In any other
    # process (bare CLI) the signal would just kill us — refuse instead.
    if signal.getsignal(signal.SIGUSR1) in (signal.SIG_DFL, signal.SIG_IGN, None):
        return tool_error(
            "no gateway restart handler in this process — ask the operator "
            "to run 'plutus gateway restart' instead")

    from harness import wake_queue
    wake_queue.enqueue("schedule", f"resuming after self-restart: {reason}",
                       source="request_desk_restart")

    # Recycle the watchers daemon too — it's a second resident interpreter
    # with the same stale-code problem (it resolves predictions every ~5s on
    # boot-time code). pm2 autorestart revives it. Non-fatal if absent.
    watchers = subprocess.run(
        ["pkill", "-TERM", "-f", "harness.watchers.run"],
        capture_output=True, text=True)

    # Drain-aware gateway self-restart: the same path as the operator's
    # /restart command. This turn finishes and returns before the exit.
    os.kill(os.getpid(), signal.SIGUSR1)
    logger.info("desk restart requested: %s", reason)

    return tool_result({
        "ok": True,
        "restarting": "gateway (after this turn drains) + watchers",
        "watchers_signaled": watchers.returncode == 0,
        "resume_wake_queued": True,
        "reason": reason,
    })


registry.register(
    name="request_desk_restart",
    toolset="spawn",
    schema=SCHEMA,
    handler=lambda args, **kw: _request_restart(args),
    description="Gracefully restart gateway+watchers to load self-patched code.",
    emoji="🔄",
)
