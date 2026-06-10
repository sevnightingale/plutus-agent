"""
Tests for media download retry logic added in PR #2982.

Covers:
- gateway/platforms/base.py:       cache_image_from_url
- gateway/platforms/slack.py:      SlackAdapter._download_slack_file
                                    SlackAdapter._download_slack_file_bytes

All async tests use asyncio.run() directly — pytest-asyncio is not installed
in this environment.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

# ---------------------------------------------------------------------------
# Helpers for building httpx exceptions
# ---------------------------------------------------------------------------

def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://example.com/img.jpg")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


def _make_timeout_error() -> httpx.TimeoutException:
    return httpx.TimeoutException("timed out")


# ---------------------------------------------------------------------------
# cache_image_from_bytes (base.py)
# ---------------------------------------------------------------------------


class TestCacheImageFromBytes:
    """Tests for gateway.platforms.base.cache_image_from_bytes"""

    def test_caches_valid_jpeg(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        path = cache_image_from_bytes(b"\xff\xd8\xff fake jpeg data", ".jpg")
        assert path.endswith(".jpg")

    def test_caches_valid_png(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        path = cache_image_from_bytes(b"\x89PNG\r\n\x1a\n fake png data", ".png")
        assert path.endswith(".png")

    def test_rejects_html_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        with pytest.raises(ValueError, match="non-image data"):
            cache_image_from_bytes(b"<!DOCTYPE html><html><title>Slack</title></html>", ".png")

    def test_rejects_empty_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        with pytest.raises(ValueError, match="non-image data"):
            cache_image_from_bytes(b"", ".jpg")

    def test_rejects_plain_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        from gateway.platforms.base import cache_image_from_bytes
        with pytest.raises(ValueError, match="non-image data"):
            cache_image_from_bytes(b"just some text, not an image", ".jpg")


# ---------------------------------------------------------------------------
# cache_image_from_url (base.py)
# ---------------------------------------------------------------------------

@patch("tools.url_safety.is_safe_url", return_value=True)
class TestCacheImageFromUrl:
    """Tests for gateway.platforms.base.cache_image_from_url"""

    def test_success_on_first_attempt(self, _mock_safe, tmp_path, monkeypatch):
        """A clean 200 response caches the image and returns a path."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        fake_response = MagicMock()
        fake_response.content = b"\xff\xd8\xff fake jpeg"
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg"
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        mock_client.get.assert_called_once()

    def test_retries_on_timeout_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A timeout on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        fake_response = MagicMock()
        fake_response.content = b"\xff\xd8\xff image data"
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_timeout_error(), fake_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_sleep = AsyncMock()

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        assert mock_client.get.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_on_429_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A 429 response on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        ok_response = MagicMock()
        ok_response.content = b"\xff\xd8\xff image data"
        ok_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_http_status_error(429), ok_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        assert mock_client.get.call_count == 2

    def test_raises_after_max_retries_exhausted(self, _mock_safe, tmp_path, monkeypatch):
        """Timeout on every attempt raises after all retries are consumed."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_timeout_error())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_image_from_url
                await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        with pytest.raises(httpx.TimeoutException):
            asyncio.run(run())

        # 3 total calls: initial + 2 retries
        assert mock_client.get.call_count == 3

    def test_non_retryable_4xx_raises_immediately(self, _mock_safe, tmp_path, monkeypatch):
        """A 404 (non-retryable) is raised immediately without any retry."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        mock_sleep = AsyncMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_http_status_error(404))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_image_from_url
                await cache_image_from_url(
                    "http://example.com/img.jpg", ext=".jpg", retries=2
                )

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(run())

        # Only 1 attempt, no sleep
        assert mock_client.get.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# cache_audio_from_url (base.py)
# ---------------------------------------------------------------------------

@patch("tools.url_safety.is_safe_url", return_value=True)
class TestCacheAudioFromUrl:
    """Tests for gateway.platforms.base.cache_audio_from_url"""

    def test_success_on_first_attempt(self, _mock_safe, tmp_path, monkeypatch):
        """A clean 200 response caches the audio and returns a path."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        fake_response = MagicMock()
        fake_response.content = b"\x00\x01 fake audio"
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg"
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        mock_client.get.assert_called_once()

    def test_retries_on_timeout_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A timeout on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        fake_response = MagicMock()
        fake_response.content = b"audio data"
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_timeout_error(), fake_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_sleep = AsyncMock()

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        assert mock_client.get.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_on_429_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A 429 response on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        ok_response = MagicMock()
        ok_response.content = b"audio data"
        ok_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_http_status_error(429), ok_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        assert mock_client.get.call_count == 2

    def test_retries_on_500_then_succeeds(self, _mock_safe, tmp_path, monkeypatch):
        """A 500 response on the first attempt is retried; second attempt succeeds."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        ok_response = MagicMock()
        ok_response.content = b"audio data"
        ok_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_http_status_error(500), ok_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_audio_from_url
                return await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        path = asyncio.run(run())
        assert path.endswith(".ogg")
        assert mock_client.get.call_count == 2

    def test_raises_after_max_retries_exhausted(self, _mock_safe, tmp_path, monkeypatch):
        """Timeout on every attempt raises after all retries are consumed."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_timeout_error())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                from gateway.platforms.base import cache_audio_from_url
                await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        with pytest.raises(httpx.TimeoutException):
            asyncio.run(run())

        # 3 total calls: initial + 2 retries
        assert mock_client.get.call_count == 3

    def test_non_retryable_4xx_raises_immediately(self, _mock_safe, tmp_path, monkeypatch):
        """A 404 (non-retryable) is raised immediately without any retry."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        mock_sleep = AsyncMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_http_status_error(404))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                from gateway.platforms.base import cache_audio_from_url
                await cache_audio_from_url(
                    "http://example.com/voice.ogg", ext=".ogg", retries=2
                )

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(run())

        # Only 1 attempt, no sleep
        assert mock_client.get.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# SSRF redirect guard tests (base.py)
# ---------------------------------------------------------------------------


class TestSSRFRedirectGuard:
    """cache_image_from_url / cache_audio_from_url must reject redirects
    that land on private/internal hosts (e.g. cloud metadata endpoint)."""

    def _make_redirect_response(self, target_url: str):
        """Build a mock httpx response that looks like a redirect."""
        resp = MagicMock()
        resp.is_redirect = True
        resp.next_request = MagicMock(url=target_url)
        return resp

    def _make_client_capturing_hooks(self):
        """Return (mock_client, captured_kwargs dict) where captured_kwargs
        will contain the kwargs passed to httpx.AsyncClient()."""
        captured = {}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        def factory(*args, **kwargs):
            captured.update(kwargs)
            return mock_client

        return mock_client, captured, factory

    def test_image_blocks_private_redirect(self, tmp_path, monkeypatch):
        """cache_image_from_url rejects a redirect to a private IP."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        redirect_resp = self._make_redirect_response(
            "http://169.254.169.254/latest/meta-data"
        )
        mock_client, captured, factory = self._make_client_capturing_hooks()

        async def fake_get(_url, **kwargs):
            # Simulate httpx calling the response event hooks
            for hook in captured["event_hooks"]["response"]:
                await hook(redirect_resp)

        mock_client.get = AsyncMock(side_effect=fake_get)

        def fake_safe(url):
            return url == "https://public.example.com/image.png"

        async def run():
            with patch("tools.url_safety.is_safe_url", side_effect=fake_safe), \
                 patch("httpx.AsyncClient", side_effect=factory):
                from gateway.platforms.base import cache_image_from_url
                await cache_image_from_url(
                    "https://public.example.com/image.png", ext=".png"
                )

        with pytest.raises(ValueError, match="Blocked redirect"):
            asyncio.run(run())

    def test_audio_blocks_private_redirect(self, tmp_path, monkeypatch):
        """cache_audio_from_url rejects a redirect to a private IP."""
        monkeypatch.setattr("gateway.platforms.base.AUDIO_CACHE_DIR", tmp_path / "audio")

        redirect_resp = self._make_redirect_response(
            "http://10.0.0.1/internal/secrets"
        )
        mock_client, captured, factory = self._make_client_capturing_hooks()

        async def fake_get(_url, **kwargs):
            for hook in captured["event_hooks"]["response"]:
                await hook(redirect_resp)

        mock_client.get = AsyncMock(side_effect=fake_get)

        def fake_safe(url):
            return url == "https://public.example.com/voice.ogg"

        async def run():
            with patch("tools.url_safety.is_safe_url", side_effect=fake_safe), \
                 patch("httpx.AsyncClient", side_effect=factory):
                from gateway.platforms.base import cache_audio_from_url
                await cache_audio_from_url(
                    "https://public.example.com/voice.ogg", ext=".ogg"
                )

        with pytest.raises(ValueError, match="Blocked redirect"):
            asyncio.run(run())

    def test_safe_redirect_allowed(self, tmp_path, monkeypatch):
        """A redirect to a public IP is allowed through."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")

        redirect_resp = self._make_redirect_response(
            "https://cdn.example.com/real-image.png"
        )

        ok_response = MagicMock()
        ok_response.content = b"\xff\xd8\xff fake jpeg"
        ok_response.raise_for_status = MagicMock()
        ok_response.is_redirect = False

        mock_client, captured, factory = self._make_client_capturing_hooks()

        call_count = 0

        async def fake_get(_url, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call triggers redirect hook, second returns data
            for hook in captured["event_hooks"]["response"]:
                await hook(redirect_resp if call_count == 1 else ok_response)
            return ok_response

        mock_client.get = AsyncMock(side_effect=fake_get)

        async def run():
            with patch("tools.url_safety.is_safe_url", return_value=True), \
                 patch("httpx.AsyncClient", side_effect=factory):
                from gateway.platforms.base import cache_image_from_url
                return await cache_image_from_url(
                    "https://public.example.com/image.png", ext=".jpg"
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")


# ---------------------------------------------------------------------------
# Slack mock setup (mirrors existing test_slack.py approach)
# ---------------------------------------------------------------------------

def _ensure_slack_mock():
    if "slack_bolt" in sys.modules and hasattr(sys.modules["slack_bolt"], "__file__"):
        return
    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler = MagicMock
    slack_sdk = MagicMock()
    slack_sdk.web.async_client.AsyncWebClient = MagicMock
    for name, mod in [
        ("slack_bolt", slack_bolt),
        ("slack_bolt.async_app", slack_bolt.async_app),
        ("slack_bolt.adapter", slack_bolt.adapter),
        ("slack_bolt.adapter.socket_mode", slack_bolt.adapter.socket_mode),
        ("slack_bolt.adapter.socket_mode.async_handler",
         slack_bolt.adapter.socket_mode.async_handler),
        ("slack_sdk", slack_sdk),
        ("slack_sdk.web", slack_sdk.web),
        ("slack_sdk.web.async_client", slack_sdk.web.async_client),
    ]:
        sys.modules.setdefault(name, mod)


_ensure_slack_mock()

import gateway.platforms.slack as _slack_mod  # noqa: E402
_slack_mod.SLACK_AVAILABLE = True

from gateway.platforms.slack import SlackAdapter  # noqa: E402
from gateway.config import Platform, PlatformConfig  # noqa: E402


def _make_slack_adapter():
    config = PlatformConfig(enabled=True, token="xoxb-fake-token")
    adapter = SlackAdapter(config)
    adapter._app = MagicMock()
    adapter._app.client = AsyncMock()
    adapter._bot_user_id = "U_BOT"
    adapter._running = True
    return adapter


# ---------------------------------------------------------------------------
# SlackAdapter._download_slack_file
# ---------------------------------------------------------------------------

class TestSlackDownloadSlackFile:
    """Tests for SlackAdapter._download_slack_file"""

    def test_success_on_first_attempt(self, tmp_path, monkeypatch):
        """Successful download on first try returns a cached file path."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        adapter = _make_slack_adapter()

        fake_response = MagicMock()
        fake_response.content = b"\x89PNG\r\n\x1a\n fake png"
        fake_response.raise_for_status = MagicMock()
        fake_response.headers = {"content-type": "image/png"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                return await adapter._download_slack_file(
                    "https://files.slack.com/img.jpg", ext=".jpg"
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        mock_client.get.assert_called_once()

    def test_rejects_html_response(self, tmp_path, monkeypatch):
        """An HTML sign-in page from Slack is rejected, not cached as image."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        adapter = _make_slack_adapter()

        fake_response = MagicMock()
        fake_response.content = b"<!DOCTYPE html><html><title>Slack</title></html>"
        fake_response.raise_for_status = MagicMock()
        fake_response.headers = {"content-type": "text/html; charset=utf-8"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                await adapter._download_slack_file(
                    "https://files.slack.com/img.jpg", ext=".jpg"
                )

        with pytest.raises(ValueError, match="HTML instead of media"):
            asyncio.run(run())

        # Verify nothing was cached
        img_dir = tmp_path / "img"
        if img_dir.exists():
            assert list(img_dir.iterdir()) == []

    def test_retries_on_timeout_then_succeeds(self, tmp_path, monkeypatch):
        """Timeout on first attempt triggers retry; success on second."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        adapter = _make_slack_adapter()

        fake_response = MagicMock()
        fake_response.content = b"\x89PNG\r\n\x1a\n image bytes"
        fake_response.raise_for_status = MagicMock()
        fake_response.headers = {"content-type": "image/png"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_timeout_error(), fake_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_sleep = AsyncMock()

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                return await adapter._download_slack_file(
                    "https://files.slack.com/img.jpg", ext=".jpg"
                )

        path = asyncio.run(run())
        assert path.endswith(".jpg")
        assert mock_client.get.call_count == 2
        mock_sleep.assert_called_once()

    def test_raises_after_max_retries(self, tmp_path, monkeypatch):
        """Timeout on every attempt eventually raises after 3 total tries."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        adapter = _make_slack_adapter()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_timeout_error())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                await adapter._download_slack_file(
                    "https://files.slack.com/img.jpg", ext=".jpg"
                )

        with pytest.raises(httpx.TimeoutException):
            asyncio.run(run())

        assert mock_client.get.call_count == 3

    def test_non_retryable_403_raises_immediately(self, tmp_path, monkeypatch):
        """A 403 is not retried; it raises immediately."""
        monkeypatch.setattr("gateway.platforms.base.IMAGE_CACHE_DIR", tmp_path / "img")
        adapter = _make_slack_adapter()

        mock_sleep = AsyncMock()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_http_status_error(403))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", mock_sleep):
                await adapter._download_slack_file(
                    "https://files.slack.com/img.jpg", ext=".jpg"
                )

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(run())

        assert mock_client.get.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# SlackAdapter._download_slack_file_bytes
# ---------------------------------------------------------------------------

class TestSlackDownloadSlackFileBytes:
    """Tests for SlackAdapter._download_slack_file_bytes"""

    def test_success_returns_bytes(self):
        """Successful download returns raw bytes."""
        adapter = _make_slack_adapter()

        fake_response = MagicMock()
        fake_response.content = b"raw bytes here"
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client):
                return await adapter._download_slack_file_bytes(
                    "https://files.slack.com/file.bin"
                )

        result = asyncio.run(run())
        assert result == b"raw bytes here"

    def test_retries_on_429_then_succeeds(self):
        """429 on first attempt is retried; raw bytes returned on second."""
        adapter = _make_slack_adapter()

        ok_response = MagicMock()
        ok_response.content = b"final bytes"
        ok_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_make_http_status_error(429), ok_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                return await adapter._download_slack_file_bytes(
                    "https://files.slack.com/file.bin"
                )

        result = asyncio.run(run())
        assert result == b"final bytes"
        assert mock_client.get.call_count == 2

    def test_raises_after_max_retries(self):
        """Persistent timeouts raise after all 3 attempts are exhausted."""
        adapter = _make_slack_adapter()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_make_timeout_error())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                await adapter._download_slack_file_bytes(
                    "https://files.slack.com/file.bin"
                )

        with pytest.raises(httpx.TimeoutException):
            asyncio.run(run())

        assert mock_client.get.call_count == 3


