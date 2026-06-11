"""Hyperliquid alerts — state-diff detection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading.integrations.hyperliquid import _client, alerts


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _client.reset_singletons_for_tests()
    monkeypatch.setenv("ACP_AGENT_WALLET", "0x000000000000000000000000000000000000dead")
    yield
    _client.reset_singletons_for_tests()


def test_position_status_change_open(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.01", "entryPx": "80000"}}
        ]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    fired, new_state = alerts.poll_hl_position_status_change(state={"positions": {}})
    assert len(fired) == 1
    assert fired[0]["kind"] == "opened"
    assert fired[0]["coin"] == "BTC"
    assert "BTC" in new_state["positions"]


def test_position_status_change_close(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {"assetPositions": []}
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    prev = {"positions": {"BTC": {"szi": 0.01, "entry_px": 80000}}}
    fired, new_state = alerts.poll_hl_position_status_change(state=prev)
    assert len(fired) == 1
    assert fired[0]["kind"] == "closed"
    assert fired[0]["coin"] == "BTC"
    assert new_state["positions"] == {}


def test_position_status_change_size_change(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.02", "entryPx": "80000"}}
        ]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    prev = {"positions": {"BTC": {"szi": 0.01, "entry_px": 80000}}}
    fired, _ = alerts.poll_hl_position_status_change(state=prev)
    assert len(fired) == 1
    assert fired[0]["kind"] == "size_changed"
    assert fired[0]["previous_szi"] == 0.01
    assert fired[0]["current_szi"] == 0.02


def test_position_status_change_no_diff(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.01", "entryPx": "80000"}}
        ]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    prev = {"positions": {"BTC": {"szi": 0.01, "entry_px": "80000"}}}
    fired, _ = alerts.poll_hl_position_status_change(state=prev)
    assert fired == []


def test_account_balance_change_above_threshold(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "marginSummary": {"accountValue": "100.0"}
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    fired, new_state = alerts.poll_hl_account_balance_change(
        state={"account_value": 50.0},
    )
    assert len(fired) == 1
    assert fired[0]["delta"] == pytest.approx(50.0)
    assert new_state["account_value"] == 100.0


def test_account_balance_change_below_threshold(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "marginSummary": {"accountValue": "100.10"}
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    # delta=0.10, threshold=max(0.50, 1.00) = 1.00 → below
    fired, _ = alerts.poll_hl_account_balance_change(state={"account_value": 100.0})
    assert fired == []


def test_alerts_no_address_returns_empty(monkeypatch):
    monkeypatch.delenv("ACP_AGENT_WALLET", raising=False)
    fired, new_state = alerts.poll_hl_position_status_change(state={"positions": {}})
    assert fired == []
    assert new_state == {"positions": {}}
