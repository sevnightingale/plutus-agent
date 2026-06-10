"""Virtuals dgclaw integration — steady-state operations + alerts.

Setup is **not** wrapped here. Plutus runs first-time setup by loading
the vendored ``skills/dgclaw/SKILL.md`` (Virtuals' own canonical
procedure) and following its instructions using terminal + the global
``acp`` binary. See ``skills/dgclaw/UPSTREAM.md`` for the rationale.

Steady-state tools shipped in this package:

- ``data_points``: leaderboard + forum reads (registered via
  ``@register_data_point``; agent-callable via ``fetch_data_point``).
- ``operations``: forum_reply, forum_create_post, dgclaw-routed trade
  open/close/positions/balance. Direct agent tools.
- ``alerts``: leaderboard rank change + perp_deposit completion.
  Polled by the watcher daemon.
"""

from . import _cli  # noqa: F401
from . import _env  # noqa: F401
from . import data_points  # noqa: F401
from . import operations  # noqa: F401
from . import alerts  # noqa: F401
