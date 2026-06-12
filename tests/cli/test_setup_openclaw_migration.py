"""Tests for OpenClaw migration integration in the setup wizard."""

from argparse import Namespace
from types import ModuleType
from unittest import mock
from unittest.mock import MagicMock, patch

from harness.cli import setup as setup_mod


# ---------------------------------------------------------------------------
# _offer_openclaw_migration — unit tests
# ---------------------------------------------------------------------------


class TestOfferOpenclawMigration:
    """Test the _offer_openclaw_migration helper in isolation."""

    def test_skips_when_no_openclaw_dir(self, tmp_path):
        """Should return False immediately when ~/.openclaw does not exist."""
        with patch("harness.cli.setup.Path.home", return_value=tmp_path):
            assert setup_mod._offer_openclaw_migration(tmp_path / ".hermes") is False

    def test_skips_when_migration_script_missing(self, tmp_path):
        """Should return False when the migration script file is absent."""
        openclaw_dir = tmp_path / ".openclaw"
        openclaw_dir.mkdir()
        with (
            patch("harness.cli.setup.Path.home", return_value=tmp_path),
            patch.object(setup_mod, "_OPENCLAW_SCRIPT", tmp_path / "nonexistent.py"),
        ):
            assert setup_mod._offer_openclaw_migration(tmp_path / ".hermes") is False

    def test_skips_when_user_declines(self, tmp_path):
        """Should return False when user declines the migration prompt."""
        openclaw_dir = tmp_path / ".openclaw"
        openclaw_dir.mkdir()
        script = tmp_path / "openclaw_to_hermes.py"
        script.write_text("# placeholder")
        with (
            patch("harness.cli.setup.Path.home", return_value=tmp_path),
            patch.object(setup_mod, "_OPENCLAW_SCRIPT", script),
            patch.object(setup_mod, "prompt_yes_no", return_value=False),
        ):
            assert setup_mod._offer_openclaw_migration(tmp_path / ".hermes") is False



    def test_handles_migration_error_gracefully(self, tmp_path):
        """Should catch exceptions and return False."""
        openclaw_dir = tmp_path / ".openclaw"
        openclaw_dir.mkdir()
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text("")

        script = tmp_path / "openclaw_to_hermes.py"
        script.write_text("# placeholder")

        with (
            patch("harness.cli.setup.Path.home", return_value=tmp_path),
            patch.object(setup_mod, "_OPENCLAW_SCRIPT", script),
            patch.object(setup_mod, "prompt_yes_no", return_value=True),
            patch.object(setup_mod, "get_config_path", return_value=config_path),
            patch(
                "importlib.util.spec_from_file_location",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = setup_mod._offer_openclaw_migration(hermes_home)

        assert result is False

    def test_creates_config_if_missing(self, tmp_path):
        """Should bootstrap config.yaml before running migration."""
        openclaw_dir = tmp_path / ".openclaw"
        openclaw_dir.mkdir()
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        # config does NOT exist yet

        script = tmp_path / "openclaw_to_hermes.py"
        script.write_text("# placeholder")

        with (
            patch("harness.cli.setup.Path.home", return_value=tmp_path),
            patch.object(setup_mod, "_OPENCLAW_SCRIPT", script),
            patch.object(setup_mod, "prompt_yes_no", return_value=True),
            patch.object(setup_mod, "get_config_path", return_value=config_path),
            patch.object(setup_mod, "load_config", return_value={"agent": {}}),
            patch.object(setup_mod, "save_config") as mock_save,
            patch(
                "importlib.util.spec_from_file_location",
                side_effect=RuntimeError("stop early"),
            ),
        ):
            setup_mod._offer_openclaw_migration(hermes_home)

        # save_config should have been called to bootstrap the file
        mock_save.assert_called_once_with({"agent": {}})


# ---------------------------------------------------------------------------
# Integration with run_setup_wizard — first-time flow
# ---------------------------------------------------------------------------


def _first_time_args() -> Namespace:
    return Namespace(
        section=None,
        non_interactive=False,
        reset=False,
    )


class TestSetupWizardOpenclawIntegration:
    """Verify _offer_openclaw_migration is called during first-time setup."""

    def test_migration_offered_during_first_time_setup(self, tmp_path):
        """On first-time setup, _offer_openclaw_migration should be called."""
        args = _first_time_args()

        with (
            patch.object(setup_mod, "ensure_hermes_home"),
            patch.object(setup_mod, "load_config", return_value={}),
            patch.object(setup_mod, "get_hermes_home", return_value=tmp_path),
            patch.object(setup_mod, "get_env_value", return_value=""),
            patch.object(setup_mod, "is_interactive_stdin", return_value=True),
            patch("harness.cli.auth.get_active_provider", return_value=None),
            # User presses Enter to start
            patch("builtins.input", return_value=""),
            # Select "Full setup" (index 1) so we exercise the full path
            patch.object(setup_mod, "prompt_choice", return_value=1),
            # Mock the migration offer
            patch.object(
                setup_mod, "_offer_openclaw_migration", return_value=False
            ) as mock_migration,
            # Mock the actual setup sections so they don't run
            patch.object(setup_mod, "setup_model_provider"),
            patch.object(setup_mod, "setup_terminal_backend"),
            patch.object(setup_mod, "setup_agent_settings"),
            patch.object(setup_mod, "setup_gateway"),
            patch.object(setup_mod, "setup_tools"),
            patch.object(setup_mod, "save_config"),
            patch.object(setup_mod, "_print_setup_summary"),
            patch.object(setup_mod, "_offer_launch_chat"),
        ):
            setup_mod.run_setup_wizard(args)

        mock_migration.assert_called_once_with(tmp_path)

    def test_migration_reloads_config_on_success(self, tmp_path):
        """When migration returns True, config should be reloaded."""
        args = _first_time_args()
        call_order = []

        def tracking_load_config():
            call_order.append("load_config")
            return {}

        with (
            patch.object(setup_mod, "ensure_hermes_home"),
            patch.object(setup_mod, "load_config", side_effect=tracking_load_config),
            patch.object(setup_mod, "get_hermes_home", return_value=tmp_path),
            patch.object(setup_mod, "get_env_value", return_value=""),
            patch.object(setup_mod, "is_interactive_stdin", return_value=True),
            patch("harness.cli.auth.get_active_provider", return_value=None),
            patch("builtins.input", return_value=""),
            patch.object(setup_mod, "prompt_choice", return_value=1),
            patch.object(setup_mod, "_offer_openclaw_migration", return_value=True),
            patch.object(setup_mod, "setup_model_provider"),
            patch.object(setup_mod, "setup_terminal_backend"),
            patch.object(setup_mod, "setup_agent_settings"),
            patch.object(setup_mod, "setup_gateway"),
            patch.object(setup_mod, "setup_tools"),
            patch.object(setup_mod, "save_config"),
            patch.object(setup_mod, "_print_setup_summary"),
            patch.object(setup_mod, "_offer_launch_chat"),
        ):
            setup_mod.run_setup_wizard(args)

        # load_config called twice: once at start, once after migration
        assert call_order.count("load_config") == 2

    def test_reloaded_config_flows_into_remaining_setup_sections(self, tmp_path):
        args = _first_time_args()
        initial_config = {}
        reloaded_config = {"model": {"provider": "openrouter"}}

        with (
            patch.object(setup_mod, "ensure_hermes_home"),
            patch.object(
                setup_mod,
                "load_config",
                side_effect=[initial_config, reloaded_config],
            ),
            patch.object(setup_mod, "get_hermes_home", return_value=tmp_path),
            patch.object(setup_mod, "get_env_value", return_value=""),
            patch.object(setup_mod, "is_interactive_stdin", return_value=True),
            patch("harness.cli.auth.get_active_provider", return_value=None),
            patch("builtins.input", return_value=""),
            patch.object(setup_mod, "prompt_choice", return_value=1),
            patch.object(setup_mod, "_offer_openclaw_migration", return_value=True),
            patch.object(setup_mod, "setup_model_provider") as setup_model_provider,
            # One patcher for the no-assertion steps — a flat list of
            # patch.object items here trips CPython's nested-block limit.
            patch.multiple(
                setup_mod,
                setup_terminal_backend=mock.DEFAULT,
                setup_agent_settings=mock.DEFAULT,
                setup_gateway=mock.DEFAULT,
                setup_tools=mock.DEFAULT,
                _setup_watchlist=mock.DEFAULT,
                _setup_hyperliquid_wallets=mock.DEFAULT,
                _setup_optional_integrations=mock.DEFAULT,
                _first_boot=mock.DEFAULT,
                save_config=mock.DEFAULT,
                _print_setup_summary=mock.DEFAULT,
                _print_desk_integrations_summary=mock.DEFAULT,
                _offer_launch_chat=mock.DEFAULT,
            ),
        ):
            setup_mod.run_setup_wizard(args)

        # Single first-time path (rebuild R5): streamlined provider step.
        setup_model_provider.assert_called_once_with(reloaded_config, quick=True)

    def test_migration_not_offered_for_existing_install(self, tmp_path):
        """Returning users should not see the migration prompt."""
        args = _first_time_args()

        with (
            patch.object(setup_mod, "ensure_hermes_home"),
            patch.object(setup_mod, "load_config", return_value={}),
            patch.object(setup_mod, "get_hermes_home", return_value=tmp_path),
            patch.object(
                setup_mod,
                "get_env_value",
                side_effect=lambda k: "sk-xxx" if k == "OPENROUTER_API_KEY" else "",
            ),
            patch("harness.cli.auth.get_active_provider", return_value=None),
            # Returning user picks "Exit"
            patch.object(setup_mod, "prompt_choice", return_value=9),
            patch.object(
                setup_mod, "_offer_openclaw_migration", return_value=False
            ) as mock_migration,
        ):
            setup_mod.run_setup_wizard(args)

        mock_migration.assert_not_called()


# ---------------------------------------------------------------------------
# _get_section_config_summary / _skip_configured_section — unit tests
# ---------------------------------------------------------------------------


class TestGetSectionConfigSummary:
    """Test the _get_section_config_summary helper."""

    def test_model_returns_none_without_api_key(self):
        with patch.object(setup_mod, "get_env_value", return_value=""):
            result = setup_mod._get_section_config_summary({}, "model")
        assert result is None

    def test_model_returns_summary_with_api_key(self):
        def env_side(key):
            return "sk-xxx" if key == "OPENROUTER_API_KEY" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary(
                {"model": "openai/gpt-4"}, "model"
            )
        assert result == "openai/gpt-4"

    def test_model_returns_dict_default_key(self):
        def env_side(key):
            return "sk-xxx" if key == "OPENAI_API_KEY" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary(
                {"model": {"default": "claude-opus-4", "provider": "anthropic"}},
                "model",
            )
        assert result == "claude-opus-4"

    def test_terminal_always_returns(self):
        with patch.object(setup_mod, "get_env_value", return_value=""):
            result = setup_mod._get_section_config_summary(
                {"terminal": {"backend": "docker"}}, "terminal"
            )
        assert result == "backend: docker"

    def test_agent_always_returns(self):
        with patch.object(setup_mod, "get_env_value", return_value=""):
            result = setup_mod._get_section_config_summary(
                {"agent": {"max_turns": 120}}, "agent"
            )
        assert result == "max turns: 120"

    def test_gateway_returns_none_without_tokens(self):
        with patch.object(setup_mod, "get_env_value", return_value=""):
            result = setup_mod._get_section_config_summary({}, "gateway")
        assert result is None

    def test_gateway_lists_platforms(self):
        def env_side(key):
            if key == "TELEGRAM_BOT_TOKEN":
                return "tok123"
            if key == "DISCORD_BOT_TOKEN":
                return "disc456"
            return ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "gateway")
        assert "Telegram" in result
        assert "Discord" in result

    def test_tools_returns_none_without_keys(self):
        with patch.object(setup_mod, "get_env_value", return_value=""):
            result = setup_mod._get_section_config_summary({}, "tools")
        assert result is None

    def test_tools_lists_configured(self):
        def env_side(key):
            return "key" if key == "BROWSERBASE_API_KEY" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "tools")
        assert "Browser" in result

    # Regression tests for issue #13025: the model / gateway summaries used
    # stale, hardcoded env-var allowlists that drifted from the real setup +
    # status flows.  Every case below would previously return ``None`` and
    # force OpenClaw migration to re-run setup for an already-configured
    # section.

    def test_model_recognises_zai_glm_api_key(self):
        """GLM_API_KEY (zai provider) should count as configured."""
        def env_side(key):
            return "glm-test-key" if key == "GLM_API_KEY" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary(
                {"model": {"provider": "zai", "default": "glm-5"}}, "model"
            )
        assert result == "glm-5"

    def test_model_recognises_minimax_api_key(self):
        """MINIMAX_API_KEY should count as configured."""
        def env_side(key):
            return "minimax-key" if key == "MINIMAX_API_KEY" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary(
                {"model": {"provider": "minimax", "default": "MiniMax-M1"}},
                "model",
            )
        assert result == "MiniMax-M1"

    def test_gateway_recognises_whatsapp_enabled(self):
        """WhatsApp uses WHATSAPP_ENABLED (not WHATSAPP_PHONE_NUMBER_ID)."""
        def env_side(key):
            return "true" if key == "WHATSAPP_ENABLED" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "gateway")
        assert result is not None
        assert "WhatsApp" in result

    def test_gateway_recognises_signal_http_url(self):
        """Signal uses SIGNAL_HTTP_URL (not SIGNAL_ACCOUNT)."""
        def env_side(key):
            return "http://signal.local" if key == "SIGNAL_HTTP_URL" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "gateway")
        assert result is not None
        assert "Signal" in result

    def test_model_ignores_bare_gh_token(self):
        """GH_TOKEN is commonly set for `gh` / git and must NOT count as a
        configured inference provider on its own — mirrors the copilot
        exclusion in resolve_provider()."""
        def env_side(key):
            return "gho_xxx" if key == "GH_TOKEN" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "model")
        assert result is None

    def test_model_ignores_bare_github_token(self):
        """GITHUB_TOKEN is commonly set in CI and must not trigger skip."""
        def env_side(key):
            return "ghp_xxx" if key == "GITHUB_TOKEN" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "model")
        assert result is None

    def test_model_ignores_claude_code_oauth_token(self):
        """CLAUDE_CODE_OAUTH_TOKEN is set by Claude Code itself and must not
        trigger skip — mirrors the _IMPLICIT_ENV_VARS guard in
        is_provider_explicitly_configured()."""
        def env_side(key):
            return "sk-ant-oat01-xxx" if key == "CLAUDE_CODE_OAUTH_TOKEN" else ""

        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary({}, "model")
        assert result is None

    def test_model_copilot_recognised_when_explicitly_chosen(self):
        """If the user picked copilot in config, GH_TOKEN *does* count —
        only the auto-detect path excludes it."""
        def env_side(key):
            return "gho_xxx" if key == "GH_TOKEN" else ""

        cfg = {"model": {"provider": "copilot", "default": "gpt-5"}}
        with patch.object(setup_mod, "get_env_value", side_effect=env_side):
            result = setup_mod._get_section_config_summary(cfg, "model")
        assert result == "gpt-5"

    def test_gateway_matches_platform_registry(self):
        """Every platform in _GATEWAY_PLATFORMS should be recognised by its
        own env-var sentinel — i.e. the summary must not drift from the
        registry used by the setup checklist."""
        for label, env_var, _fn in setup_mod._GATEWAY_PLATFORMS:
            def env_side(key, _target=env_var):
                return "x" if key == _target else ""
            with patch.object(setup_mod, "get_env_value", side_effect=env_side):
                result = setup_mod._get_section_config_summary({}, "gateway")
            expected = setup_mod._gateway_platform_short_label(label)
            assert result is not None, f"{label} ({env_var}) not recognised"
            assert expected in result, (
                f"{label} ({env_var}) recognised but label missing from summary: {result!r}"
            )


class TestSkipConfiguredSection:
    """Test the _skip_configured_section helper."""

    def test_returns_false_when_not_configured(self):
        with patch.object(setup_mod, "get_env_value", return_value=""):
            result = setup_mod._skip_configured_section({}, "model", "Model")
        assert result is False

    def test_returns_true_when_user_skips(self):
        def env_side(key):
            return "sk-xxx" if key == "OPENROUTER_API_KEY" else ""

        with (
            patch.object(setup_mod, "get_env_value", side_effect=env_side),
            patch.object(setup_mod, "prompt_yes_no", return_value=False),
        ):
            result = setup_mod._skip_configured_section(
                {"model": "openai/gpt-4"}, "model", "Model"
            )
        assert result is True

    def test_returns_false_when_user_wants_reconfig(self):
        def env_side(key):
            return "sk-xxx" if key == "OPENROUTER_API_KEY" else ""

        with (
            patch.object(setup_mod, "get_env_value", side_effect=env_side),
            patch.object(setup_mod, "prompt_yes_no", return_value=True),
        ):
            result = setup_mod._skip_configured_section(
                {"model": "openai/gpt-4"}, "model", "Model"
            )
        assert result is False


class TestSetupWizardSkipsConfiguredSections:
    """After migration, already-configured sections should offer skip."""

