"""Unified-session redirect (Manor-mention) tests for TelegramAdapter.

Covers ``_maybe_redirect_event``: the inbound transform that routes group
@mentions of the bot (or replies-to-bot) into the operator's unified DM
session.  See ``gateway/platforms/telegram.py:_load_unified_redirect_bindings``
for the full design.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


# Helpers ─────────────────────────────────────────────────────────────


def _make_adapter(bindings=None, bot_username="plutus_agent_bot", bot_id=99999999):
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="fake-token",
        extra={"unified_session_redirect": bindings or []},
    )
    adapter._bot = SimpleNamespace(username=bot_username, id=bot_id)
    # Borrow the real loader so we exercise the parser logic too.
    from gateway.platforms.telegram import TelegramAdapter as _T
    adapter._unified_redirect_bindings = _T._load_unified_redirect_bindings(adapter)
    return adapter


def _mention_entity(text: str, username: str):
    """Build a MessageEntity-like object for @mention detection."""
    needle = f"@{username}"
    idx = text.find(needle)
    if idx < 0:
        raise ValueError(f"@{username} not in {text!r}")
    return SimpleNamespace(type="mention", offset=idx, length=len(needle), user=None)


def _make_raw_group_message(
    text: str,
    chat_id: str = "-1003793147758",
    chat_title: str = "Nightingale Manor",
    sender_id: int = 1054536871,
    sender_name: str = "Sev",
    mention_username: str = None,
    reply_to_bot_id: int = None,
):
    entities = []
    if mention_username:
        entities.append(_mention_entity(text, mention_username))
    reply = None
    if reply_to_bot_id is not None:
        reply = SimpleNamespace(
            from_user=SimpleNamespace(id=reply_to_bot_id),
            message_id=999,
            text="prior bot message",
            caption=None,
        )
    return SimpleNamespace(
        text=text,
        caption=None,
        entities=entities,
        caption_entities=None,
        reply_to_message=reply,
        chat=SimpleNamespace(id=int(chat_id), type="supergroup", title=chat_title),
        message_id=1234,
        from_user=SimpleNamespace(id=sender_id, full_name=sender_name),
        date=None,
    )


def _make_event(raw, source: SessionSource, text: str = None) -> MessageEvent:
    return MessageEvent(
        text=text or (raw.text or ""),
        message_type=MessageType.TEXT,
        source=source,
        raw_message=raw,
        message_id=str(raw.message_id) if raw.message_id else None,
    )


def _binding(
    source_group_id="-1003793147758",
    target_dm="1054536871",
    target_user="1054536871",
    marker="[MANOR MENTION",
):
    return {
        "source_group_id": source_group_id,
        "target_dm_chat_id": target_dm,
        "target_dm_user_id": target_user,
        "marker_prefix": marker,
    }


# ── _load_unified_redirect_bindings ──────────────────────────────────


def test_loader_empty_when_unset():
    adapter = _make_adapter(bindings=None)
    assert adapter._unified_redirect_bindings == {}


def test_loader_parses_valid_entry():
    adapter = _make_adapter(bindings=[_binding()])
    assert "-1003793147758" in adapter._unified_redirect_bindings
    entry = adapter._unified_redirect_bindings["-1003793147758"]
    assert entry["target_dm_chat_id"] == "1054536871"
    assert entry["target_dm_user_id"] == "1054536871"
    assert entry["marker_prefix"] == "[MANOR MENTION"


def test_loader_defaults_target_user_to_chat_id():
    bindings = [
        {"source_group_id": "-1003793147758", "target_dm_chat_id": "1054536871"},
    ]
    adapter = _make_adapter(bindings=bindings)
    assert adapter._unified_redirect_bindings["-1003793147758"]["target_dm_user_id"] == "1054536871"


def test_loader_skips_malformed_entries():
    bindings = [
        {"source_group_id": "-1003793147758", "target_dm_chat_id": "1054536871"},  # valid
        "not-a-dict",  # invalid
        {"source_group_id": "-1009999999"},  # missing target_dm_chat_id
        {"target_dm_chat_id": "555"},  # missing source_group_id
    ]
    adapter = _make_adapter(bindings=bindings)
    assert list(adapter._unified_redirect_bindings.keys()) == ["-1003793147758"]


def test_loader_handles_non_list_input():
    adapter = _make_adapter(bindings={"this is not": "a list"})
    assert adapter._unified_redirect_bindings == {}


# ── _maybe_redirect_event ────────────────────────────────────────────


def test_no_redirect_when_no_bindings():
    adapter = _make_adapter(bindings=None)
    raw = _make_raw_group_message(
        "@plutus_agent_bot ping", mention_username="plutus_agent_bot"
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    assert out is event  # unchanged


def test_no_redirect_for_unconfigured_group():
    adapter = _make_adapter(bindings=[_binding(source_group_id="-1003793147758")])
    raw = _make_raw_group_message(
        "@plutus_agent_bot ping",
        chat_id="-1009999999",  # different group
        mention_username="plutus_agent_bot",
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1009999999",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    assert out is event


def test_no_redirect_without_mention_or_reply():
    adapter = _make_adapter(bindings=[_binding()])
    raw = _make_raw_group_message(
        "Just chatting with Yuna, nothing for Plutus",
        # No mention, no reply
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    assert out is event


def test_redirect_on_mention():
    adapter = _make_adapter(bindings=[_binding()])
    raw = _make_raw_group_message(
        "@plutus_agent_bot what's your current read?",
        mention_username="plutus_agent_bot",
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)

    assert out is not event  # new event
    # Session-key drivers: source now points to DM
    assert out.source.chat_id == "1054536871"
    assert out.source.chat_type == "dm"
    assert out.source.user_id == "1054536871"
    # Reply target preserved (group)
    assert out.reply_to_chat_id == "-1003793147758"
    # Marker prepended to text
    assert out.text.startswith("[MANOR MENTION")
    assert "from Sev" in out.text
    assert "at Nightingale Manor" in out.text
    assert "@plutus_agent_bot what's your current read?" in out.text
    # Internal + synthetic_kind set so authz / cron-mode policy applies
    assert out.internal is True
    assert out.synthetic_kind == "manor_mention:-1003793147758"
    # Original message_id / raw preserved for threading
    assert out.message_id == "1234"
    assert out.raw_message is raw


def test_redirect_on_reply_to_bot():
    adapter = _make_adapter(bindings=[_binding()], bot_id=99999999)
    raw = _make_raw_group_message(
        "thanks for that",
        reply_to_bot_id=99999999,  # reply to Plutus bot
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    assert out is not event
    assert out.source.chat_id == "1054536871"
    assert out.reply_to_chat_id == "-1003793147758"
    assert out.synthetic_kind == "manor_mention:-1003793147758"


def test_no_redirect_on_reply_to_other_user():
    adapter = _make_adapter(bindings=[_binding()], bot_id=99999999)
    raw = _make_raw_group_message(
        "thanks for that",
        reply_to_bot_id=12345,  # NOT the bot
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    assert out is event


def test_no_redirect_for_dm_messages():
    adapter = _make_adapter(bindings=[_binding()])
    raw = SimpleNamespace(
        text="hi plutus",
        caption=None,
        entities=[],
        caption_entities=None,
        reply_to_message=None,
        chat=SimpleNamespace(id=1054536871, type="private", title=None),
        message_id=1,
        from_user=SimpleNamespace(id=1054536871, full_name="Sev"),
        date=None,
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="1054536871",
        chat_type="dm",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    assert out is event  # DMs never redirect (chat is not a configured group)


def test_redirect_preserves_attachments():
    adapter = _make_adapter(bindings=[_binding()])
    raw = _make_raw_group_message(
        "@plutus_agent_bot look at this",
        mention_username="plutus_agent_bot",
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = MessageEvent(
        text=raw.text,
        message_type=MessageType.PHOTO,
        source=source,
        raw_message=raw,
        message_id="1234",
        media_urls=["/tmp/a.jpg", "/tmp/b.jpg"],
        media_types=["image/jpeg", "image/jpeg"],
    )
    out = adapter._maybe_redirect_event(event)
    assert out.media_urls == ["/tmp/a.jpg", "/tmp/b.jpg"]
    assert out.media_types == ["image/jpeg", "image/jpeg"]
    assert out.message_type == MessageType.PHOTO


def test_redirect_marker_includes_sender_and_chat():
    adapter = _make_adapter(bindings=[_binding()])
    raw = _make_raw_group_message(
        "@plutus_agent_bot test",
        sender_name="Sebastian",
        mention_username="plutus_agent_bot",
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="999",
        user_name="Sebastian",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    first_line = out.text.split("\n", 1)[0]
    assert first_line.startswith("[MANOR MENTION")
    assert "from Sebastian" in first_line
    assert "at Nightingale Manor" in first_line


# ── MessageEvent.reply_to_chat_id forward to outbound delivery ──────


def test_target_chat_id_falls_back_to_source_when_no_override():
    """When ``reply_to_chat_id`` is None, _process_message_background should
    derive _target_chat_id from event.source.chat_id (no behaviour change).

    We can't easily run the full background loop in a unit test, but we can
    assert the field defaults to None so the fallback path engages.
    """
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_type="dm",
        user_id="123",
    )
    event = MessageEvent(text="hi", source=source)
    assert event.reply_to_chat_id is None
    assert event.reply_to_thread_id is None


def test_redirect_sets_reply_to_chat_id_so_delivery_routes_to_group():
    """The redirect's whole point: outbound delivery target = original group."""
    adapter = _make_adapter(bindings=[_binding()])
    raw = _make_raw_group_message(
        "@plutus_agent_bot test", mention_username="plutus_agent_bot"
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003793147758",
        chat_type="group",
        user_id="1054536871",
        user_name="Sev",
    )
    event = _make_event(raw, source)
    out = adapter._maybe_redirect_event(event)
    # After redirect, the delivery helper in base.py does:
    #   _target_chat_id = event.reply_to_chat_id or event.source.chat_id
    # which must resolve to the Manor group_id.
    target = out.reply_to_chat_id or out.source.chat_id
    assert target == "-1003793147758"
