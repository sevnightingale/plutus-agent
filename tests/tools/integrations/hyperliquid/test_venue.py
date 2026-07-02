"""Hyperliquid venue — verify the dispatch shape with a mocked Exchange.

We patch ``get_exchange`` so the venue functions exercise their full
payload assembly + response normalisation logic without hitting the
network or needing an API wallet key.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading.integrations.hyperliquid import _client, venue


@pytest.fixture(autouse=True)
def _reset_singletons():
    _client.reset_singletons_for_tests()
    yield
    _client.reset_singletons_for_tests()


class _FakeInfo:
    """Stub of the SDK Info's cached meta maps (size rounding lookups)."""
    name_to_coin = {"BTC": "BTC", "ETH": "ETH"}
    coin_to_asset = {"BTC": 0, "ETH": 1}
    asset_to_sz_decimals = {0: 5, 1: 4}


@pytest.fixture(autouse=True)
def _fake_info(monkeypatch):
    monkeypatch.setattr(venue, "get_info", lambda: _FakeInfo())


def _make_filled_response(avg_px: float, size: float, oid: int = 12345):
    return {
        "response": {
            "data": {
                "statuses": [{
                    "filled": {
                        "avgPx": str(avg_px),
                        "totalSz": str(size),
                        "oid": oid,
                    }
                }]
            }
        }
    }


def test_hl_place_order_market_long(monkeypatch):
    mock_ex = MagicMock()
    mock_ex.market_open.return_value = _make_filled_response(80000.0, 0.01)
    monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

    res = venue.hl_place_order(symbol="BTC", side="long", size=0.01)
    assert res["fill_price"] == 80000.0
    assert res["size"] == 0.01
    assert res["order_id"] == "12345"

    mock_ex.market_open.assert_called_once()
    kwargs = mock_ex.market_open.call_args.kwargs
    assert kwargs["name"] == "BTC"
    assert kwargs["is_buy"] is True
    assert kwargs["sz"] == 0.01


def test_hl_place_order_market_short(monkeypatch):
    mock_ex = MagicMock()
    mock_ex.market_open.return_value = _make_filled_response(80000.0, 0.01)
    monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

    venue.hl_place_order(symbol="BTC", side="short", size=0.01)
    assert mock_ex.market_open.call_args.kwargs["is_buy"] is False


def test_hl_place_order_limit_requires_px(monkeypatch):
    monkeypatch.setattr(venue, "get_exchange", lambda: MagicMock())
    with pytest.raises(ValueError, match="limit_px required"):
        venue.hl_place_order(symbol="BTC", side="long", size=0.01, order_type="limit")


class TestRoundSzForHl:
    """Size flooring to szDecimals — the 2026-07-02 first-trade killer.

    A raw risk-derived size (0.00025113643744465553 BTC) reached the SDK,
    whose float_to_wire REJECTS sub-szDecimals precision instead of
    rounding. The venue layer now floors size like it already rounds price.
    """

    def test_floors_raw_risk_derived_size(self):
        assert venue._round_sz_for_hl("BTC", 0.00025113643744465553) == 0.00025

    def test_valid_size_unchanged(self):
        assert venue._round_sz_for_hl("BTC", 0.01) == 0.01

    def test_float_artifact_does_not_underfloor(self):
        # 0.29 * 1e4 == 2899.9999... in binary; must floor to 0.29, not 0.2899
        assert venue._round_sz_for_hl("ETH", 0.29) == 0.29

    def test_zero_floor_is_refused(self):
        with pytest.raises(ValueError, match="floors to 0"):
            venue._round_sz_for_hl("BTC", 0.000001)

    def test_market_order_sends_floored_size(self, monkeypatch):
        mock_ex = MagicMock()
        mock_ex.market_open.return_value = _make_filled_response(62000.0, 0.00025)
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)
        venue.hl_place_order(symbol="BTC", side="long",
                             size=0.00025113643744465553)
        assert mock_ex.market_open.call_args.kwargs["sz"] == 0.00025


def test_hl_close_position_routes_to_market_close(monkeypatch):
    mock_ex = MagicMock()
    mock_ex.market_close.return_value = _make_filled_response(81500.0, 0.01)
    monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

    res = venue.hl_close_position(symbol="ETH", position_id=42)
    assert res["fill_price"] == 81500.0
    mock_ex.market_close.assert_called_once_with(coin="ETH", slippage=venue.DEFAULT_MARKET_SLIPPAGE)


def test_hl_cancel_order(monkeypatch):
    mock_ex = MagicMock()
    mock_ex.cancel.return_value = {"status": "ok"}
    monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

    res = venue.hl_cancel_order(venue_order_id=999, symbol="BTC")
    assert res["venue_order_id"] == 999
    mock_ex.cancel.assert_called_once_with("BTC", 999)


def test_hl_modify_order_requires_size(monkeypatch):
    mock_ex = MagicMock()
    monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)
    with pytest.raises(ValueError, match="new_size"):
        venue.hl_modify_order(
            venue_order_id=1, new_limit_px=50000.0,
            symbol="BTC", side="long",
        )


def test_normalize_response_resting_raises():
    resp = {
        "response": {
            "data": {
                "statuses": [{"resting": {"oid": 555}}]
            }
        }
    }
    with pytest.raises(RuntimeError, match="rested without fill"):
        venue._normalize_response(resp)


def test_normalize_response_error_surfaces():
    resp = {
        "response": {
            "data": {
                "statuses": [{"error": "insufficient margin"}]
            }
        }
    }
    with pytest.raises(RuntimeError, match="insufficient margin"):
        venue._normalize_response(resp)
