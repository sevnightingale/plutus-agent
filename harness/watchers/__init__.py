"""Watcher daemon — polls registered alerts, emits wake events.

Runs as a separate pm2 process (``plutus-watchers``). Reads the
PLUTUS alert registry, polls each alert on its declared cadence
(respecting per-alert ``throttle_seconds``), and writes wake events
to ``~/.plutus-agent/wake_events.ndjson``. The gateway tails that
file and turns each wake event into a message routed to Plutus.

The state (last-seen positions, last-seen account value, throttle
bookkeeping) lives in ``~/.plutus-agent/watcher_state.json``.
"""
