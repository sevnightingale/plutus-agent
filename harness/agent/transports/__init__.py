"""Transport layer types and registry for provider response normalization.

Usage:
    from harness.agent.transports import get_transport
    transport = get_transport("anthropic_messages")
    result = transport.normalize_response(raw_response)
"""

from harness.agent.transports.types import NormalizedResponse, ToolCall, Usage, build_tool_call, map_finish_reason  # noqa: F401

_REGISTRY: dict = {}


def register_transport(api_mode: str, transport_cls: type) -> None:
    """Register a transport class for an api_mode string."""
    _REGISTRY[api_mode] = transport_cls


def get_transport(api_mode: str):
    """Get a transport instance for the given api_mode.

    Returns None if no transport is registered for this api_mode.
    This allows gradual migration — call sites can check for None
    and fall back to the legacy code path.
    """
    if not _REGISTRY:
        _discover_transports()
    cls = _REGISTRY.get(api_mode)
    if cls is None:
        # A mode can be missing from a NON-empty registry when this module
        # was re-imported (fresh _REGISTRY) while the transport submodules
        # stayed cached in sys.modules — their import-time registration
        # never re-ran. Discovery re-registers from the cached modules.
        _discover_transports()
        cls = _REGISTRY.get(api_mode)
    if cls is None:
        return None
    return cls()


def _discover_transports() -> None:
    """Register every transport module's (api_mode, class) pair.

    Imports each module and registers from its module attributes rather
    than relying on import-time side effects alone: if a module is already
    cached in sys.modules, a bare import is a no-op and would leave a
    freshly re-imported registry permanently missing that mode.
    """
    import importlib

    for mod_name in ("anthropic", "codex", "chat_completions", "bedrock"):
        try:
            mod = importlib.import_module(f"harness.agent.transports.{mod_name}")
        except ImportError:
            continue
        api_mode = getattr(mod, "API_MODE", None)
        transport_cls = getattr(mod, "TRANSPORT_CLS", None)
        if api_mode and transport_cls:
            _REGISTRY.setdefault(api_mode, transport_cls)
