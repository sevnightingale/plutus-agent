"""
Shared platform registry for plutus-agent.

Single source of truth for platform metadata consumed by both
skills_config (label display) and tools_config (default toolset
resolution).  Import ``PLATFORMS`` from here instead of maintaining
duplicate dicts in each module.
"""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """Metadata for a single platform entry."""
    label: str
    default_toolset: str


# Ordered so that TUI menus are deterministic.
# plutus-agent-cli is the desk surface — the gateway session IS plutus-main,
# so every operator-facing platform defaults to it (a config.yaml
# `platform_toolsets` entry still overrides per platform). api_server keeps
# its curated upstream set (deliberately restricted — no interactive tools).
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="plutus-agent-cli")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="plutus-agent-cli")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="plutus-agent-cli")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="plutus-agent-cli")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="plutus-agent-cli")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="hermes-api-server")),
])


def platform_label(key: str, default: str = "") -> str:
    """Return the display label for a platform key, or *default*."""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
