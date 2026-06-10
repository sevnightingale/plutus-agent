"""Hyperliquid integration — read-only data points + signed-execution venue.

Imported for side-effects (each submodule registers entries with the
core registries via decorators). Order matters: client → accounts → data
points → venue → alerts. Outcome computation is a helper module called
by the venue's ``close_position_fn``.
"""

from . import _client  # noqa: F401  (must initialise before others)
from . import accounts  # noqa: F401
from . import data_points  # noqa: F401
from . import venue  # noqa: F401
from . import alerts  # noqa: F401
