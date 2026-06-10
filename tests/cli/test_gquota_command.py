from unittest.mock import MagicMock, patch


def test_gquota_uses_chat_console_when_tui_is_live():
    from harness.agent.google_oauth import GoogleOAuthError
    from harness.repl import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.console = MagicMock()
    cli._app = object()

    live_console = MagicMock()

    with patch("harness.repl.ChatConsole", return_value=live_console), \
         patch("harness.agent.google_oauth.get_valid_access_token", side_effect=GoogleOAuthError("No Google OAuth credentials found")), \
         patch("harness.agent.google_oauth.load_credentials", return_value=None), \
         patch("harness.agent.google_code_assist.retrieve_user_quota"):
        cli._handle_gquota_command("/gquota")

    assert live_console.print.call_count == 2
    cli.console.print.assert_not_called()
