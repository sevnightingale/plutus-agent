"""Send Message Tool -- cross-channel messaging via platform APIs.

Sends a message to a user or channel on any connected messaging platform
(Telegram, Discord, Slack). Supports listing available targets and resolving
human-friendly channel names to IDs. Works in both CLI and gateway contexts.
"""

import asyncio
import json
import logging
import os
import re
from typing import Dict, Optional
import ssl
import time

from harness.agent.redact import redact_sensitive_text

logger = logging.getLogger(__name__)

_TELEGRAM_TOPIC_TARGET_RE = re.compile(r"^\s*(-?\d+)(?::(\d+))?\s*$")
# Discord snowflake IDs are numeric, same regex pattern as Telegram topic targets.
_NUMERIC_TOPIC_RE = _TELEGRAM_TOPIC_TARGET_RE
# Platforms that address recipients by phone number and accept E.164 format
# (with a leading '+'). Without this, "+15551234567" fails the isdigit() check
# below and falls through to channel-name resolution, which has no way to
# resolve a raw phone number. Keeping the '+' preserves the E.164 form that
# downstream adapters expect.
_PHONE_PLATFORMS = frozenset()
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".3gp"}
_AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".m4a"}
_VOICE_EXTS = {".ogg", ".opus"}
_URL_SECRET_QUERY_RE = re.compile(
    r"([?&](?:access_token|api[_-]?key|auth[_-]?token|token|signature|sig)=)([^&#\s]+)",
    re.IGNORECASE,
)
_GENERIC_SECRET_ASSIGN_RE = re.compile(
    r"\b(access_token|api[_-]?key|auth[_-]?token|signature|sig)\s*=\s*([^\s,;]+)",
    re.IGNORECASE,
)


def _sanitize_error_text(text) -> str:
    """Redact secrets from error text before surfacing it to users/models."""
    redacted = redact_sensitive_text(text)
    redacted = _URL_SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}***", redacted)
    redacted = _GENERIC_SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}=***", redacted)
    return redacted


def _error(message: str) -> dict:
    """Build a standardized error payload with redacted content."""
    return {"error": _sanitize_error_text(message)}


def _telegram_retry_delay(exc: Exception, attempt: int) -> float | None:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            return 1.0

    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return None
    if (
        "bad gateway" in text
        or "502" in text
        or "too many requests" in text
        or "429" in text
        or "service unavailable" in text
        or "503" in text
        or "gateway timeout" in text
        or "504" in text
    ):
        return float(2 ** attempt)
    return None


async def _send_telegram_message_with_retry(bot, *, attempts: int = 3, **kwargs):
    for attempt in range(attempts):
        try:
            return await bot.send_message(**kwargs)
        except Exception as exc:
            delay = _telegram_retry_delay(exc, attempt)
            if delay is None or attempt >= attempts - 1:
                raise
            logger.warning(
                "Transient Telegram send failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                attempts,
                delay,
                _sanitize_error_text(exc),
            )
            await asyncio.sleep(delay)


SEND_MESSAGE_SCHEMA = {
    "name": "send_message",
    "description": (
        "Send a message to a connected messaging platform, or list available targets.\n\n"
        "IMPORTANT: When the user asks to send to a specific channel or person "
        "(not just a bare platform name), call send_message(action='list') FIRST to see "
        "available targets, then send to the correct one.\n"
        "If the user just says a platform name like 'send to telegram', send directly "
        "to the home channel without listing first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "list"],
                "description": "Action to perform. 'send' (default) sends a message. 'list' returns all available channels/contacts across connected platforms."
            },
            "target": {
                "type": "string",
                "description": "Delivery target. Format: 'platform' (uses home channel), 'platform:#channel-name', 'platform:chat_id', or 'platform:chat_id:thread_id' for Telegram topics and Discord threads. Examples: 'telegram', 'telegram:-1001234567890:17585', 'discord:999888777:555444333', 'discord:#bot-home', 'slack:#engineering', 'signal:+155****4567', 'matrix:!roomid:server.org', 'matrix:@user:server.org'"
            },
            "message": {
                "type": "string",
                "description": "The message text to send"
            }
        },
        "required": []
    }
}


def send_message_tool(args, **kw):
    """Handle cross-channel send_message tool calls."""
    action = args.get("action", "send")

    if action == "list":
        return _handle_list()

    return _handle_send(args)


def _handle_list():
    """Return formatted list of available messaging targets."""
    try:
        from harness.gateway.channel_directory import format_directory_for_display
        return json.dumps({"targets": format_directory_for_display()})
    except Exception as e:
        return json.dumps(_error(f"Failed to load channel directory: {e}"))


def _handle_send(args):
    """Send a message to a platform target."""
    target = args.get("target", "")
    message = args.get("message", "")
    if not target or not message:
        return tool_error("Both 'target' and 'message' are required when action='send'")

    parts = target.split(":", 1)
    platform_name = parts[0].strip().lower()
    target_ref = parts[1].strip() if len(parts) > 1 else None
    chat_id = None
    thread_id = None

    if target_ref:
        chat_id, thread_id, is_explicit = _parse_target_ref(platform_name, target_ref)
    else:
        is_explicit = False

    # Resolve human-friendly channel names to numeric IDs
    if target_ref and not is_explicit:
        try:
            from harness.gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_name, target_ref)
            if resolved:
                chat_id, thread_id, _ = _parse_target_ref(platform_name, resolved)
            else:
                return json.dumps({
                    "error": f"Could not resolve '{target_ref}' on {platform_name}. "
                    f"Use send_message(action='list') to see available targets."
                })
        except Exception:
            return json.dumps({
                "error": f"Could not resolve '{target_ref}' on {platform_name}. "
                f"Try using a numeric channel ID instead."
            })

    from harness.tools.interrupt import is_interrupted
    if is_interrupted():
        return tool_error("Interrupted")

    try:
        from harness.gateway.config import load_gateway_config, Platform
        config = load_gateway_config()
    except Exception as e:
        return json.dumps(_error(f"Failed to load gateway config: {e}"))

    platform_map = {
        "telegram": Platform.TELEGRAM,
        "discord": Platform.DISCORD,
        "slack": Platform.SLACK,
    }
    platform = platform_map.get(platform_name)
    if not platform:
        avail = ", ".join(platform_map.keys())
        return tool_error(f"Unknown platform: {platform_name}. Available: {avail}")

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        return tool_error(f"Platform '{platform_name}' is not configured. Set up credentials in ~/.plutus-agent/config.yaml or environment variables.")

    from harness.gateway.platforms.base import BasePlatformAdapter

    media_files, cleaned_message = BasePlatformAdapter.extract_media(message)
    mirror_text = cleaned_message.strip() or _describe_media_for_mirror(media_files)

    used_home_channel = False
    if not chat_id:
        home = config.get_home_channel(platform)
        if home:
            chat_id = home.chat_id
            used_home_channel = True
        else:
            return json.dumps({
                "error": f"No home channel set for {platform_name} to determine where to send the message. "
                f"Either specify a channel directly with '{platform_name}:CHANNEL_NAME', "
                f"or set a home channel via: plutus config set {platform_name.upper()}_HOME_CHANNEL <channel_id>"
            })

    duplicate_skip = _maybe_skip_cron_duplicate_send(platform_name, chat_id, thread_id)
    if duplicate_skip:
        return json.dumps(duplicate_skip)

    try:
        from harness.model_tools import _run_async
        result = _run_async(
            _send_to_platform(
                platform,
                pconfig,
                chat_id,
                cleaned_message,
                thread_id=thread_id,
                media_files=media_files,
            )
        )
        if used_home_channel and isinstance(result, dict) and result.get("success"):
            result["note"] = f"Sent to {platform_name} home channel (chat_id: {chat_id})"

        # Mirror the sent message into the target's gateway session
        if isinstance(result, dict) and result.get("success") and mirror_text:
            try:
                from harness.gateway.mirror import mirror_to_session
                from harness.gateway.session_context import get_session_env
                source_label = get_session_env("HERMES_SESSION_PLATFORM", "cli")
                if mirror_to_session(platform_name, chat_id, mirror_text, source_label=source_label, thread_id=thread_id):
                    result["mirrored"] = True
            except Exception:
                pass

        if isinstance(result, dict) and "error" in result:
            result["error"] = _sanitize_error_text(result["error"])
        return json.dumps(result)
    except Exception as e:
        return json.dumps(_error(f"Send failed: {e}"))


def _parse_target_ref(platform_name: str, target_ref: str):
    """Parse a tool target into chat_id/thread_id and whether it is explicit."""
    if platform_name == "telegram":
        match = _TELEGRAM_TOPIC_TARGET_RE.fullmatch(target_ref)
        if match:
            return match.group(1), match.group(2), True
    if platform_name == "discord":
        match = _NUMERIC_TOPIC_RE.fullmatch(target_ref)
        if match:
            return match.group(1), match.group(2), True
    if target_ref.lstrip("-").isdigit():
        return target_ref, None, True
    return None, None, False


def _describe_media_for_mirror(media_files):
    """Return a human-readable mirror summary when a message only contains media."""
    if not media_files:
        return ""
    if len(media_files) == 1:
        media_path, is_voice = media_files[0]
        ext = os.path.splitext(media_path)[1].lower()
        if is_voice and ext in _VOICE_EXTS:
            return "[Sent voice message]"
        if ext in _IMAGE_EXTS:
            return "[Sent image attachment]"
        if ext in _VIDEO_EXTS:
            return "[Sent video attachment]"
        if ext in _AUDIO_EXTS:
            return "[Sent audio attachment]"
        return "[Sent document attachment]"
    return f"[Sent {len(media_files)} media attachments]"


def _get_cron_auto_delivery_target():
    """Return the cron scheduler's auto-delivery target for the current run, if any."""
    from harness.gateway.session_context import get_session_env
    platform = get_session_env("HERMES_CRON_AUTO_DELIVER_PLATFORM", "").strip().lower()
    chat_id = get_session_env("HERMES_CRON_AUTO_DELIVER_CHAT_ID", "").strip()
    if not platform or not chat_id:
        return None
    thread_id = get_session_env("HERMES_CRON_AUTO_DELIVER_THREAD_ID", "").strip() or None
    return {
        "platform": platform,
        "chat_id": chat_id,
        "thread_id": thread_id,
    }


def _maybe_skip_cron_duplicate_send(platform_name: str, chat_id: str, thread_id: str | None):
    """Skip redundant cron send_message calls when the scheduler will auto-deliver there."""
    auto_target = _get_cron_auto_delivery_target()
    if not auto_target:
        return None

    same_target = (
        auto_target["platform"] == platform_name
        and str(auto_target["chat_id"]) == str(chat_id)
        and auto_target.get("thread_id") == thread_id
    )
    if not same_target:
        return None

    target_label = f"{platform_name}:{chat_id}"
    if thread_id is not None:
        target_label += f":{thread_id}"

    return {
        "success": True,
        "skipped": True,
        "reason": "cron_auto_delivery_duplicate_target",
        "target": target_label,
        "note": (
            f"Skipped send_message to {target_label}. This cron job will already auto-deliver "
            "its final response to that same target. Put the intended user-facing content in "
            "your final response instead, or use a different target if you want an additional message."
        ),
    }


async def _send_to_platform(platform, pconfig, chat_id, message, thread_id=None, media_files=None):
    """Route a message to the appropriate platform sender.

    Long messages are automatically chunked to fit within platform limits
    using the same smart-splitting algorithm as the gateway adapters
    (preserves code-block boundaries, adds part indicators).
    """
    from harness.gateway.config import Platform
    from harness.gateway.platforms.base import BasePlatformAdapter, utf16_len
    from harness.gateway.platforms.discord import DiscordAdapter
    from harness.gateway.platforms.slack import SlackAdapter

    # Telegram adapter import is optional (requires python-telegram-bot)
    try:
        from harness.gateway.platforms.telegram import TelegramAdapter
        _telegram_available = True
    except ImportError:
        _telegram_available = False

    media_files = media_files or []

    if platform == Platform.SLACK and message:
        try:
            slack_adapter = SlackAdapter.__new__(SlackAdapter)
            message = slack_adapter.format_message(message)
        except Exception:
            logger.debug("Failed to apply Slack mrkdwn formatting in _send_to_platform", exc_info=True)

    # Platform message length limits (from adapter class attributes)
    _MAX_LENGTHS = {
        Platform.TELEGRAM: TelegramAdapter.MAX_MESSAGE_LENGTH if _telegram_available else 4096,
        Platform.DISCORD: DiscordAdapter.MAX_MESSAGE_LENGTH,
        Platform.SLACK: SlackAdapter.MAX_MESSAGE_LENGTH,
    }

    # Smart-chunk the message to fit within platform limits.
    # For short messages or platforms without a known limit this is a no-op.
    # Telegram measures length in UTF-16 code units, not Unicode codepoints.
    max_len = _MAX_LENGTHS.get(platform)
    if max_len:
        _len_fn = utf16_len if platform == Platform.TELEGRAM else None
        chunks = BasePlatformAdapter.truncate_message(message, max_len, len_fn=_len_fn)
    else:
        chunks = [message]

    # --- Telegram: special handling for media attachments ---
    if platform == Platform.TELEGRAM:
        last_result = None
        disable_link_previews = bool(getattr(pconfig, "extra", {}) and pconfig.extra.get("disable_link_previews"))
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            result = await _send_telegram(
                pconfig.token,
                chat_id,
                chunk,
                media_files=media_files if is_last else [],
                thread_id=thread_id,
                disable_link_previews=disable_link_previews,
            )
            if isinstance(result, dict) and result.get("error"):
                return result
            last_result = result
        return last_result

    # --- Discord: special handling for media attachments ---
    if platform == Platform.DISCORD:
        last_result = None
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            result = await _send_discord(
                pconfig.token,
                chat_id,
                chunk,
                media_files=media_files if is_last else [],
                thread_id=thread_id,
            )
            if isinstance(result, dict) and result.get("error"):
                return result
            last_result = result
        return last_result

    # --- Non-media platforms ---
    if media_files and not message.strip():
        return {
            "error": (
                f"send_message MEDIA delivery is currently only supported for telegram and discord; "
                f"target {platform.value} had only media attachments"
            )
        }
    warning = None
    if media_files:
        warning = (
            f"MEDIA attachments were omitted for {platform.value}; "
            "native send_message media delivery is currently only supported for telegram and discord"
        )

    last_result = None
    for chunk in chunks:
        if platform == Platform.SLACK:
            result = await _send_slack(pconfig.token, chat_id, chunk)
        else:
            result = {"error": f"Direct sending not yet implemented for {platform.value}"}

        if isinstance(result, dict) and result.get("error"):
            return result
        last_result = result

    if warning and isinstance(last_result, dict) and last_result.get("success"):
        warnings = list(last_result.get("warnings", []))
        warnings.append(warning)
        last_result["warnings"] = warnings
    return last_result


async def _send_telegram(token, chat_id, message, media_files=None, thread_id=None, disable_link_previews=False):
    """Send via Telegram Bot API (one-shot, no polling needed).

    Applies markdown→MarkdownV2 formatting (same as the gateway adapter)
    so that bold, links, and headers render correctly.  If the message
    already contains HTML tags, it is sent with ``parse_mode='HTML'``
    instead, bypassing MarkdownV2 conversion.
    """
    try:
        from telegram import Bot
        from telegram.constants import ParseMode

        # Auto-detect HTML tags — if present, skip MarkdownV2 and send as HTML.
        # Inspired by github.com/ashaney — PR #1568.
        _has_html = bool(re.search(r'<[a-zA-Z/][^>]*>', message))

        if _has_html:
            formatted = message
            send_parse_mode = ParseMode.HTML
        else:
            # Reuse the gateway adapter's format_message for markdown→MarkdownV2
            try:
                from harness.gateway.platforms.telegram import TelegramAdapter
                _adapter = TelegramAdapter.__new__(TelegramAdapter)
                formatted = _adapter.format_message(message)
            except Exception:
                # Fallback: send as-is if formatting unavailable
                formatted = message
            send_parse_mode = ParseMode.MARKDOWN_V2

        bot = Bot(token=token)
        int_chat_id = int(chat_id)
        media_files = media_files or []
        thread_kwargs = {}
        if thread_id is not None:
            thread_kwargs["message_thread_id"] = int(thread_id)
        if disable_link_previews:
            thread_kwargs["disable_web_page_preview"] = True

        last_msg = None
        warnings = []

        if formatted.strip():
            try:
                last_msg = await _send_telegram_message_with_retry(
                    bot,
                    chat_id=int_chat_id, text=formatted,
                    parse_mode=send_parse_mode, **thread_kwargs
                )
            except Exception as md_error:
                # Parse failed, fall back to plain text
                if "parse" in str(md_error).lower() or "markdown" in str(md_error).lower() or "html" in str(md_error).lower():
                    logger.warning(
                        "Parse mode %s failed in _send_telegram, falling back to plain text: %s",
                        send_parse_mode,
                        _sanitize_error_text(md_error),
                    )
                    if not _has_html:
                        try:
                            from harness.gateway.platforms.telegram import _strip_mdv2
                            plain = _strip_mdv2(formatted)
                        except Exception:
                            plain = message
                    else:
                        plain = message
                    last_msg = await _send_telegram_message_with_retry(
                        bot,
                        chat_id=int_chat_id, text=plain,
                        parse_mode=None, **thread_kwargs
                    )
                else:
                    raise

        for media_path, is_voice in media_files:
            if not os.path.exists(media_path):
                warning = f"Media file not found, skipping: {media_path}"
                logger.warning(warning)
                warnings.append(warning)
                continue

            ext = os.path.splitext(media_path)[1].lower()
            try:
                with open(media_path, "rb") as f:
                    if ext in _IMAGE_EXTS:
                        last_msg = await bot.send_photo(
                            chat_id=int_chat_id, photo=f, **thread_kwargs
                        )
                    elif ext in _VIDEO_EXTS:
                        last_msg = await bot.send_video(
                            chat_id=int_chat_id, video=f, **thread_kwargs
                        )
                    elif ext in _VOICE_EXTS and is_voice:
                        last_msg = await bot.send_voice(
                            chat_id=int_chat_id, voice=f, **thread_kwargs
                        )
                    elif ext in _AUDIO_EXTS:
                        last_msg = await bot.send_audio(
                            chat_id=int_chat_id, audio=f, **thread_kwargs
                        )
                    else:
                        last_msg = await bot.send_document(
                            chat_id=int_chat_id, document=f, **thread_kwargs
                        )
            except Exception as e:
                warning = _sanitize_error_text(f"Failed to send media {media_path}: {e}")
                logger.error(warning)
                warnings.append(warning)

        if last_msg is None:
            error = "No deliverable text or media remained after processing MEDIA tags"
            if warnings:
                return {"error": error, "warnings": warnings}
            return {"error": error}

        result = {
            "success": True,
            "platform": "telegram",
            "chat_id": chat_id,
            "message_id": str(last_msg.message_id),
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except ImportError:
        return {"error": "python-telegram-bot not installed. Run: pip install python-telegram-bot"}
    except Exception as e:
        return _error(f"Telegram send failed: {e}")


def _derive_forum_thread_name(message: str) -> str:
    """Derive a thread name from the first line of the message, capped at 100 chars."""
    first_line = message.strip().split("\n", 1)[0].strip()
    # Strip common markdown heading prefixes
    first_line = first_line.lstrip("#").strip()
    if not first_line:
        first_line = "New Post"
    return first_line[:100]


# Process-local cache for Discord channel-type probes.  Avoids re-probing the
# same channel on every send when the directory cache has no entry (e.g. fresh
# install, or channel created after the last directory build).
_DISCORD_CHANNEL_TYPE_PROBE_CACHE: Dict[str, bool] = {}


def _remember_channel_is_forum(chat_id: str, is_forum: bool) -> None:
    _DISCORD_CHANNEL_TYPE_PROBE_CACHE[str(chat_id)] = bool(is_forum)


def _probe_is_forum_cached(chat_id: str) -> Optional[bool]:
    return _DISCORD_CHANNEL_TYPE_PROBE_CACHE.get(str(chat_id))


async def _send_discord(token, chat_id, message, thread_id=None, media_files=None):
    """Send a single message via Discord REST API (no websocket client needed).

    Chunking is handled by _send_to_platform() before this is called.

    When thread_id is provided, the message is sent directly to that thread
    via the /channels/{thread_id}/messages endpoint.

    Media files are uploaded one-by-one via multipart/form-data after the
    text message is sent (same pattern as Telegram).

    Forum channels (type 15) reject POST /messages — a thread post is created
    automatically via POST /channels/{id}/threads.  Media files are uploaded
    as multipart attachments on the starter message of the new thread.

    Channel type is resolved from the channel directory first, then a
    process-local probe cache, and only as a last resort with a live
    GET /channels/{id} probe (whose result is memoized).
    """
    try:
        import aiohttp
    except ImportError:
        return {"error": "aiohttp not installed. Run: pip install aiohttp"}
    try:
        from harness.gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp
        _proxy = resolve_proxy_url(platform_env_var="DISCORD_PROXY")
        _sess_kw, _req_kw = proxy_kwargs_for_aiohttp(_proxy)
        auth_headers = {"Authorization": f"Bot {token}"}
        json_headers = {**auth_headers, "Content-Type": "application/json"}
        media_files = media_files or []
        last_data = None
        warnings = []

        # Thread endpoint: Discord threads are channels; send directly to the thread ID.
        if thread_id:
            url = f"https://discord.com/api/v10/channels/{thread_id}/messages"
        else:
            # Check if the target channel is a forum channel (type 15).
            # Forum channels reject POST /messages — create a thread post instead.
            # Three-layer detection: directory cache → process-local probe
            # cache → GET /channels/{id} probe (with result memoized).
            _channel_type = None
            try:
                from harness.gateway.channel_directory import lookup_channel_type
                _channel_type = lookup_channel_type("discord", chat_id)
            except Exception:
                pass

            if _channel_type == "forum":
                is_forum = True
            elif _channel_type is not None:
                is_forum = False
            else:
                cached = _probe_is_forum_cached(chat_id)
                if cached is not None:
                    is_forum = cached
                else:
                    is_forum = False
                    try:
                        info_url = f"https://discord.com/api/v10/channels/{chat_id}"
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), **_sess_kw) as info_sess:
                            async with info_sess.get(info_url, headers=json_headers, **_req_kw) as info_resp:
                                if info_resp.status == 200:
                                    info = await info_resp.json()
                                    is_forum = info.get("type") == 15
                                    _remember_channel_is_forum(chat_id, is_forum)
                    except Exception:
                        logger.debug("Failed to probe channel type for %s", chat_id, exc_info=True)

            if is_forum:
                thread_name = _derive_forum_thread_name(message)
                thread_url = f"https://discord.com/api/v10/channels/{chat_id}/threads"

                # Filter to readable media files up front so we can pick the
                # right code path (JSON vs multipart) before opening a session.
                valid_media = []
                for media_path, _is_voice in media_files:
                    if not os.path.exists(media_path):
                        warning = f"Media file not found, skipping: {media_path}"
                        logger.warning(warning)
                        warnings.append(warning)
                        continue
                    valid_media.append(media_path)

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), **_sess_kw) as session:
                    if valid_media:
                        # Multipart: payload_json + files[N] creates a forum
                        # thread with the starter message plus attachments in
                        # a single API call.
                        attachments_meta = [
                            {"id": str(idx), "filename": os.path.basename(path)}
                            for idx, path in enumerate(valid_media)
                        ]
                        starter_message = {"content": message, "attachments": attachments_meta}
                        payload_json = json.dumps({"name": thread_name, "message": starter_message})

                        form = aiohttp.FormData()
                        form.add_field("payload_json", payload_json, content_type="application/json")

                        # Buffer file bytes up front — aiohttp's FormData can
                        # read lazily and we don't want handles closing under
                        # it on retry.
                        try:
                            for idx, media_path in enumerate(valid_media):
                                with open(media_path, "rb") as fh:
                                    form.add_field(
                                        f"files[{idx}]",
                                        fh.read(),
                                        filename=os.path.basename(media_path),
                                    )
                            async with session.post(thread_url, headers=auth_headers, data=form, **_req_kw) as resp:
                                if resp.status not in (200, 201):
                                    body = await resp.text()
                                    return _error(f"Discord forum thread creation error ({resp.status}): {body}")
                                data = await resp.json()
                        except Exception as e:
                            return _error(_sanitize_error_text(f"Discord forum thread upload failed: {e}"))
                    else:
                        # No media — simple JSON POST creates the thread with
                        # just the text starter.
                        async with session.post(
                            thread_url,
                            headers=json_headers,
                            json={
                                "name": thread_name,
                                "message": {"content": message},
                            },
                            **_req_kw,
                        ) as resp:
                            if resp.status not in (200, 201):
                                body = await resp.text()
                                return _error(f"Discord forum thread creation error ({resp.status}): {body}")
                            data = await resp.json()

                thread_id_created = data.get("id")
                starter_msg_id = (data.get("message") or {}).get("id", thread_id_created)
                result = {
                    "success": True,
                    "platform": "discord",
                    "chat_id": chat_id,
                    "thread_id": thread_id_created,
                    "message_id": starter_msg_id,
                }
                if warnings:
                    result["warnings"] = warnings
                return result

            url = f"https://discord.com/api/v10/channels/{chat_id}/messages"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), **_sess_kw) as session:
            # Send text message (skip if empty and media is present)
            if message.strip() or not media_files:
                async with session.post(url, headers=json_headers, json={"content": message}, **_req_kw) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        return _error(f"Discord API error ({resp.status}): {body}")
                    last_data = await resp.json()

            # Send each media file as a separate multipart upload
            for media_path, _is_voice in media_files:
                if not os.path.exists(media_path):
                    warning = f"Media file not found, skipping: {media_path}"
                    logger.warning(warning)
                    warnings.append(warning)
                    continue
                try:
                    form = aiohttp.FormData()
                    filename = os.path.basename(media_path)
                    with open(media_path, "rb") as f:
                        form.add_field("files[0]", f, filename=filename)
                        async with session.post(url, headers=auth_headers, data=form, **_req_kw) as resp:
                            if resp.status not in (200, 201):
                                body = await resp.text()
                                warning = _sanitize_error_text(f"Failed to send media {media_path}: Discord API error ({resp.status}): {body}")
                                logger.error(warning)
                                warnings.append(warning)
                                continue
                            last_data = await resp.json()
                except Exception as e:
                    warning = _sanitize_error_text(f"Failed to send media {media_path}: {e}")
                    logger.error(warning)
                    warnings.append(warning)

        if last_data is None:
            error = "No deliverable text or media remained after processing"
            if warnings:
                return {"error": error, "warnings": warnings}
            return {"error": error}

        result = {"success": True, "platform": "discord", "chat_id": chat_id, "message_id": last_data.get("id")}
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return _error(f"Discord send failed: {e}")


async def _send_slack(token, chat_id, message):
    """Send via Slack Web API."""
    try:
        import aiohttp
    except ImportError:
        return {"error": "aiohttp not installed. Run: pip install aiohttp"}
    try:
        from harness.gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp
        _proxy = resolve_proxy_url()
        _sess_kw, _req_kw = proxy_kwargs_for_aiohttp(_proxy)
        url = "https://slack.com/api/chat.postMessage"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), **_sess_kw) as session:
            payload = {"channel": chat_id, "text": message, "mrkdwn": True}
            async with session.post(url, headers=headers, json=payload, **_req_kw) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return {"success": True, "platform": "slack", "chat_id": chat_id, "message_id": data.get("ts")}
                return _error(f"Slack API error: {data.get('error', 'unknown')}")
    except Exception as e:
        return _error(f"Slack send failed: {e}")


def _check_send_message():
    """Gate send_message on gateway running (always available on messaging platforms)."""
    from harness.gateway.session_context import get_session_env
    platform = get_session_env("HERMES_SESSION_PLATFORM", "")
    if platform and platform != "local":
        return True
    try:
        from harness.gateway.status import is_gateway_running
        return is_gateway_running()
    except Exception:
        return False




# --- Registry ---
from harness.tools.registry import registry, tool_error

registry.register(
    name="send_message",
    toolset="messaging",
    schema=SEND_MESSAGE_SCHEMA,
    handler=send_message_tool,
    check_fn=_check_send_message,
    emoji="📨",
)
