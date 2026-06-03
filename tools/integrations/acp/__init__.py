"""Virtuals ACP integration — agent-driven setup + steady-state wallet/identity tools.

Subprocess-wraps the global ``acp`` CLI (npm package
``@virtuals-protocol/acp-cli``). All agent-facing tools are registered
via this package's ``__init__.py`` side-effect imports so the AST
scanner picks them up and the Plutus tool surface includes them when
the ``acp`` toolset is enabled.

Setup tools (acp_install_check, acp_configure, acp_agent_create,
acp_agent_add_signer, acp_wallet_topup) are used during the
``trading/bootstrap-setup`` skill on first run. Steady-state tools
(acp_wallet_balance, acp_browse_offerings, acp_wallet_send, etc.)
run during ongoing operation.
"""

from . import _cli  # noqa: F401
from . import _env  # noqa: F401
from . import setup as setup_tools  # noqa: F401
from . import data_points  # noqa: F401
from . import operations  # noqa: F401
from . import identity  # noqa: F401
from . import accounts  # noqa: F401
from . import events  # noqa: F401
