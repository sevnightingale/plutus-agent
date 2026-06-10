"""Plutus lifecycle queries + standard event-type registrations.

Each query tool file (`query_*.py`, `find_similar_*.py`, `inspect_position.py`)
registers itself via `registry.register(...)` at module load — the AST
scanner picks them up.

`event_types.py` registers the standard lifecycle event handlers via
`@register_event` decorators. The AST scanner skips it (no
`registry.register` call) so we side-effect import it here, mirroring
how integration packages bootstrap their decorator registrations.
"""

from . import event_types  # noqa: F401
