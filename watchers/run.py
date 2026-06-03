"""Watcher daemon entrypoint.

Loaded as a long-running pm2 process. Discovers all registered tools
(side-effect: integration packages get imported and PLUTUS-style
@register_alert decorators populate alert_registry). Then loops:

    every TICK_SECONDS:
        for each registered alert:
            if it's time to poll (per throttle):
                run its poll_fn, persist state, emit wake events

Tick is fast (~1s); per-alert throttle keeps actual polling at the
declared cadence (alerts default 60s with 300s throttle).

Run via `pm2 start ecosystem.config.js --only plutus-watchers`
or directly as `python -m watchers.run`.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

logger = logging.getLogger(__name__)


TICK_SECONDS = 5      # how often the daemon wakes to evaluate which alerts are due
SHUTDOWN = False


def _shutdown_handler(signum, frame):
    global SHUTDOWN
    logger.info("watcher daemon received signal %s — shutting down", signum)
    SHUTDOWN = True


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    _setup_logging()
    logger.info("plutus-watchers starting")

    # Import all registered tools (side effect: integration packages load,
    # @register_alert decorators populate alert_registry).
    from tools.registry import discover_builtin_tools
    discover_builtin_tools()

    from tools.core.alert_registry import list_all
    from .poller import poll_once

    alerts = list_all()
    logger.info("registered alerts: %s", [a.name for a in alerts] or "(none)")

    if not alerts:
        logger.warning("no alerts registered — daemon idle. Exiting.")
        return 0

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    from .poller import schedule_wake_session

    last_tick_log = time.time()
    while not SHUTDOWN:
        tick_events = []
        for entry in alerts:
            try:
                tick_events.extend(poll_once(entry))
            except Exception as exc:
                logger.exception("poll cycle failed for '%s': %s", entry.name, exc)

        if tick_events:
            try:
                job = schedule_wake_session(tick_events)
                if job:
                    logger.info(
                        "watcher batched %d event(s) → cron job %s (skill=%s)",
                        len(tick_events), job.get("id"), job.get("skill"),
                    )
            except Exception as exc:
                logger.exception("schedule_wake_session failed: %s", exc)

        # Heartbeat log every 5 min so operators see the daemon's alive
        if (time.time() - last_tick_log) > 300:
            logger.info("watcher tick (alerts=%d)", len(alerts))
            last_tick_log = time.time()

        time.sleep(TICK_SECONDS)

    logger.info("plutus-watchers exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
