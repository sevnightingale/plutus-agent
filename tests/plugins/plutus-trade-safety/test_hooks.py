"""HALT kill-switch + trade-notify hook tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import importlib
import harness.plugins as plugins
plugins_trade_safety = importlib.import_module("harness.plugins.plutus-trade-safety")


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import importlib; import harness.constants as plutus_constants
    importlib.reload(plutus_constants)
    return tmp_path


def test_halt_blocks_place_order(temp_home):
    halt = temp_home / "HALT"
    halt.write_text("Operator pause for end-of-day review")

    res = plugins_trade_safety._on_pre_tool_call(
        tool_name="place_order",
        args={"venue": "hyperliquid", "symbol": "BTC", "side": "long", "size": 0.01},
    )
    assert res is not None
    assert res["action"] == "block"
    assert "HALT file present" in res["message"]
    assert "Operator pause" in res["message"]


def test_halt_blocks_close_position(temp_home):
    (temp_home / "HALT").write_text("")
    res = plugins_trade_safety._on_pre_tool_call(
        tool_name="close_position",
        args={"venue": "hyperliquid", "position_id": 1},
    )
    assert res["action"] == "block"


def test_halt_blocks_acp_wallet_send(temp_home):
    (temp_home / "HALT").write_text("")
    res = plugins_trade_safety._on_pre_tool_call(
        tool_name="acp_wallet_send",
        args={"chain_id": "1", "to": "0xabc", "amount": "1", "token": "USDC"},
    )
    assert res["action"] == "block"


def test_no_halt_no_block(temp_home):
    # No HALT file
    res = plugins_trade_safety._on_pre_tool_call(
        tool_name="place_order",
        args={"venue": "hyperliquid"},
    )
    assert res is None


def test_halt_does_not_block_non_trade_tools(temp_home):
    (temp_home / "HALT").write_text("")
    res = plugins_trade_safety._on_pre_tool_call(
        tool_name="fetch_data_point",
        args={"name": "hl_price"},
    )
    assert res is None


def test_post_tool_call_no_chat_id_skips_send(temp_home, monkeypatch):
    monkeypatch.delenv("PLUTUS_TRADE_CHAT_ID", raising=False)
    monkeypatch.setattr(plugins_trade_safety, "_resolve_trade_chat_id", lambda: None)
    sent = []
    monkeypatch.setattr(plugins_trade_safety, "_send_telegram", lambda c, t: sent.append((c, t)))
    plugins_trade_safety._on_post_tool_call(
        tool_name="place_order",
        args={"venue": "hyperliquid", "symbol": "BTC", "side": "long", "size": 0.01},
        result=json.dumps({"fill_price": 80000.0}),
    )
    assert sent == []


def test_post_tool_call_sends_for_trade(temp_home, monkeypatch):
    monkeypatch.setattr(plugins_trade_safety, "_resolve_trade_chat_id", lambda: "123456")
    sent = []
    monkeypatch.setattr(plugins_trade_safety, "_send_telegram", lambda c, t: sent.append((c, t)))
    plugins_trade_safety._on_post_tool_call(
        tool_name="place_order",
        args={"venue": "hyperliquid", "symbol": "BTC", "side": "long",
              "size": 0.01, "conviction": 0.7},
        result=json.dumps({"fill_price": 80000.5}),
    )
    assert len(sent) == 1
    chat, msg = sent[0]
    assert chat == "123456"
    assert "place_order" in msg
    assert "BTC" in msg
    assert "long" in msg
    assert "px=80000.5" in msg


def test_post_tool_call_skips_non_trade(temp_home, monkeypatch):
    monkeypatch.setattr(plugins_trade_safety, "_resolve_trade_chat_id", lambda: "123456")
    sent = []
    monkeypatch.setattr(plugins_trade_safety, "_send_telegram", lambda c, t: sent.append((c, t)))
    plugins_trade_safety._on_post_tool_call(
        tool_name="fetch_data_point",
        args={"name": "hl_price"},
        result="{}",
    )
    assert sent == []


def test_register_wires_both_hooks():
    ctx = MagicMock()
    plugins_trade_safety.register(ctx)
    calls = [c.args[0] for c in ctx.register_hook.call_args_list]
    assert "pre_tool_call" in calls
    assert "post_tool_call" in calls
