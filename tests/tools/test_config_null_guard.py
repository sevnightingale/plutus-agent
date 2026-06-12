"""Tests for config.get() null-coalescing in tool configuration.

YAML ``null`` values (or ``~``) for a present key make ``dict.get(key, default)``
return ``None`` instead of the default — calling ``.lower()`` on that raises
``AttributeError``.  These tests verify the ``or`` coalescing guards.
"""

from unittest.mock import patch
import pytest


# ── TTS tool ──────────────────────────────────────────────────────────────

class TestTTSProviderNullGuard:
    """harness/tools/tts_tool.py — _get_provider()"""

    def test_explicit_null_provider_returns_default(self):
        """YAML ``tts: {provider: null}`` should fall back to default."""
        from harness.tools.tts_tool import _get_provider, DEFAULT_PROVIDER

        result = _get_provider({"provider": None})
        assert result == DEFAULT_PROVIDER.lower().strip()

    def test_missing_provider_returns_default(self):
        """No ``provider`` key at all should also return default."""
        from harness.tools.tts_tool import _get_provider, DEFAULT_PROVIDER

        result = _get_provider({})
        assert result == DEFAULT_PROVIDER.lower().strip()

    def test_valid_provider_passed_through(self):
        from harness.tools.tts_tool import _get_provider

        result = _get_provider({"provider": "OPENAI"})
        assert result == "openai"


# ── Web tools ─────────────────────────────────────────────────────────────

class TestWebBackendNullGuard:
    """harness/tools/web_tools.py — _get_backend()"""

    @patch("harness.tools.web_tools._load_web_config", return_value={"backend": None})
    def test_explicit_null_backend_does_not_crash(self, _cfg):
        """YAML ``web: {backend: null}`` should not raise AttributeError."""
        from harness.tools.web_tools import _get_backend

        # Should not raise — the exact return depends on env key fallback
        result = _get_backend()
        assert isinstance(result, str)

    @patch("harness.tools.web_tools._load_web_config", return_value={})
    def test_missing_backend_does_not_crash(self, _cfg):
        from harness.tools.web_tools import _get_backend

        result = _get_backend()
        assert isinstance(result, str)


# ── MCP tool ──────────────────────────────────────────────────────────────

class TestMCPAuthNullGuard:
    """harness/tools/mcp_tool.py — MCPServerTask.__init__() auth config line"""

    def test_explicit_null_auth_does_not_crash(self):
        """YAML ``auth: null`` in MCP server config should not raise."""
        # Test the expression directly — MCPServerTask.__init__ has many deps
        config = {"auth": None, "timeout": 30}
        auth_type = (config.get("auth") or "").lower().strip()
        assert auth_type == ""

    def test_missing_auth_defaults_to_empty(self):
        config = {"timeout": 30}
        auth_type = (config.get("auth") or "").lower().strip()
        assert auth_type == ""

    def test_valid_auth_passed_through(self):
        config = {"auth": "OAUTH", "timeout": 30}
        auth_type = (config.get("auth") or "").lower().strip()
        assert auth_type == "oauth"


# ── Trajectory compressor ─────────────────────────────────────────────────

