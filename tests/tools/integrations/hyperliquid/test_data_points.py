"""Hyperliquid data points — schema + live-endpoint smoke tests.

Symbol-level data points hit live HL public endpoints (no credentials);
account-state data points loud-fail without ACP_AGENT_WALLET — both
cases are exercised so a regression that swallows the failure (e.g. a
hidden fallback to localhost) shows up here.
"""

from __future__ import annotations

import os

import pytest

from trading.perception.core import data_point_registry, account_registry, venue_registry, alert_registry
from trading.integrations.hyperliquid import _client


@pytest.fixture(autouse=True)
def _reset_hl_singletons():
    _client.reset_singletons_for_tests()
    yield
    _client.reset_singletons_for_tests()


def _import_integration():
    """Re-import the HL integration package so decorators re-register.

    The dispatcher tests reset all registries between cases, so by the
    time HL tests run the integration's prior registrations are gone.
    Python's module cache means a plain import is a no-op; reload forces
    the module body (and its register_*() decorator calls) to re-execute.
    """
    import importlib
    from trading.perception.core import (
        data_point_registry, account_registry, venue_registry,
        alert_registry, identity_registry,
    )
    data_point_registry.reset()
    account_registry.reset()
    venue_registry.reset()
    alert_registry.reset()
    identity_registry.reset()

    import trading.integrations.hyperliquid as hl_pkg
    importlib.reload(hl_pkg._client)
    importlib.reload(hl_pkg.accounts)
    importlib.reload(hl_pkg.data_points)
    importlib.reload(hl_pkg.venue)
    importlib.reload(hl_pkg.alerts)
    importlib.reload(hl_pkg)


def test_all_nine_data_points_registered():
    _import_integration()
    names = {e.name for e in data_point_registry.list_all(source="hyperliquid")}
    assert names == {
        "hl_price",
        "hl_candles",
        "hl_orderbook",
        "hl_funding_and_oi",
        "hl_universe",
        "hl_holdings",
        "hl_total_equity",
        "hl_drawdown_from_peak",
        "hl_trade_readiness",
    }


def test_hl_trading_account_registered():
    _import_integration()
    entry = account_registry.lookup("hl_trading")
    assert entry.purpose == "trading_capital"
    assert entry.venue == "hyperliquid"
    assert entry.chain == "hyperliquid"


def test_hyperliquid_venue_registered():
    _import_integration()
    venue = venue_registry.lookup("hyperliquid")
    assert venue.place_order_fn is not None
    assert venue.close_position_fn is not None
    assert venue.modify_order_fn is not None
    assert venue.cancel_order_fn is not None
    assert venue.account_state_fn is not None
    assert venue.outcome_compute_fn is not None


def test_alerts_registered():
    _import_integration()
    names = {a.name for a in alert_registry.list_all(source="hyperliquid")}
    assert names == {
        "hl_position_status_change",
        "hl_account_balance_change",
        "hl_price_range",
        "hl_prediction_resolution",
        "hl_position_alert",
    }


# ─── Live HL public endpoint tests ────────────────────────────────────────


@pytest.mark.integration
def test_hl_price_live():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_price
    res = hl_price("BTC")
    assert res["symbol"] == "BTC"
    assert isinstance(res["price"], float)
    assert res["price"] > 0
    assert "ts_ms" in res


@pytest.mark.integration
def test_hl_price_unknown_symbol():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_price
    with pytest.raises(KeyError, match="not in Hyperliquid universe"):
        hl_price("NOTAREALSYMBOLXYZ")


@pytest.mark.integration
def test_hl_candles_live():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_candles
    res = hl_candles("BTC", "1m", lookback_bars=10)
    assert res["symbol"] == "BTC"
    assert res["interval"] == "1m"
    assert res["count"] >= 1
    c = res["candles"][0]
    assert all(k in c for k in ("t", "o", "h", "l", "c", "v"))


def test_hl_candles_invalid_interval():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_candles
    with pytest.raises(ValueError, match="Unknown candle interval"):
        hl_candles("BTC", "999z", lookback_bars=5)


@pytest.mark.integration
def test_hl_orderbook_live():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_orderbook
    res = hl_orderbook("BTC", depth=5)
    assert res["symbol"] == "BTC"
    assert len(res["bids"]) <= 5
    assert len(res["asks"]) <= 5
    if res["bids"] and res["asks"]:
        assert res["bids"][0]["px"] < res["asks"][0]["px"]


@pytest.mark.integration
def test_hl_funding_and_oi_live():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_funding_and_oi
    res = hl_funding_and_oi("BTC")
    assert res["symbol"] == "BTC"
    assert "funding" in res
    assert "open_interest" in res


@pytest.mark.integration
def test_hl_universe_live():
    _import_integration()
    from trading.integrations.hyperliquid.data_points import hl_universe
    res = hl_universe()
    assert res["count"] >= 50  # HL has hundreds of perps
    names = {a["name"] for a in res["universe"]}
    assert "BTC" in names
    assert "ETH" in names


# ─── Account-state data points need ACP_AGENT_WALLET ──────────────────────


def _force_no_registered_address(monkeypatch):
    """Make resolve_account_address see neither a registry address nor env.

    Another test on the same worker may have registered hl_trading WITH an
    address (the account registry is module-level state) — clearing only the
    env var isn't enough to exercise the loud-fail path.
    """
    monkeypatch.delenv("ACP_AGENT_WALLET", raising=False)
    monkeypatch.setattr(
        _client, "lookup_account",
        lambda name: (_ for _ in ()).throw(KeyError(name)),
    )


def test_hl_holdings_loud_fails_without_address(monkeypatch):
    _import_integration()
    _force_no_registered_address(monkeypatch)
    from trading.integrations.hyperliquid.data_points import hl_holdings
    with pytest.raises(_client.HLConfigError):
        hl_holdings("hl_trading")


def test_hl_total_equity_loud_fails_without_address(monkeypatch):
    _import_integration()
    _force_no_registered_address(monkeypatch)
    from trading.integrations.hyperliquid.data_points import hl_total_equity
    with pytest.raises(_client.HLConfigError):
        hl_total_equity("hl_trading")
