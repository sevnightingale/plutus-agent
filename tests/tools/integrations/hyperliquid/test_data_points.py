"""Hyperliquid data points — schema + live-endpoint smoke tests.

Symbol-level data points hit live HL public endpoints (no credentials);
account-state data points loud-fail without ACP_AGENT_WALLET — both
cases are exercised so a regression that swallows the failure (e.g. a
hidden fallback to localhost) shows up here.
"""

from __future__ import annotations

import os

import time
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


def test_all_eleven_data_points_registered():
    _import_integration()
    names = {e.name for e in data_point_registry.list_all(source="hyperliquid")}
    assert names == {
        "hl_price",
        "hl_candles",
        "hl_orderbook",
        "hl_funding_and_oi",
        "hl_book_imbalance",
        "hl_funding_zscore",
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


class TestDrawdownFromPeak:
    """The peak must come from real history, and only from valid readings.

    Two faults sat on top of each other. The lookback cutoff was computed in
    MILLISECONDS against a ``ts`` column stored in SECONDS, so the query
    matched no row ever written: peak collapsed to the current reading and
    drawdown was 0.00% by construction — an alert that could not fire. Fixing
    the units alone then surfaced the second fault, because
    ``equity_usd = spot_usdc + perp_account_value`` double-counts while a
    position is open (2026-07-04: spot $73.52 + perp $73.46 = $146.99 on a
    ~$73 account), which manufactures a 50% drawdown out of a flat account.
    """

    def _fixture(self, monkeypatch, rows, current):
        import json as _json
        import trading.integrations.hyperliquid.data_points as dp

        addr = "0xabc"

        class _Conn:
            def execute(self, _sql, params):
                cutoff = params[0]
                assert cutoff < 1e12, (
                    "cutoff must be epoch SECONDS; a millisecond cutoff "
                    "matches nothing and silently zeroes the drawdown"
                )
                kept = [r for r in rows if r["ts"] >= cutoff]
                out = [{"value_json": _json.dumps(r["v"]), "ts": r["ts"]}
                       for r in kept]

                class _Cur:
                    def fetchall(self_inner):
                        return out

                return _Cur()

        monkeypatch.setattr(dp, "resolve_account_address", lambda _n: addr)
        monkeypatch.setattr(dp, "get_info", lambda: object())
        monkeypatch.setattr(dp, "hl_total_equity", lambda _n: {
            "account_name": "hl_trading", "address": addr,
            "equity_usd": current["equity_usd"],
            "perp_account_value": current.get("perp_account_value", 0.0)})
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda: _Conn())
        return dp

    @staticmethod
    def _row(ts, equity, perp=0.0, addr="0xabc"):
        return {"ts": ts, "v": {"account_name": "hl_trading", "address": addr,
                                "equity_usd": equity, "spot_usdc": equity,
                                "perp_account_value": perp}}

    def test_history_is_actually_read(self, monkeypatch):
        now = time.time()
        dp = self._fixture(monkeypatch, [
            self._row(now - 10 * 86400, 100.0),
            self._row(now - 5 * 86400, 120.0),
        ], {"equity_usd": 90.0})
        out = dp.hl_drawdown_from_peak("hl_trading")
        assert out["samples"] == 3
        assert out["peak_equity_usd"] == pytest.approx(120.0)
        assert out["drawdown_pct"] == pytest.approx(25.0)

    def test_in_position_readings_never_become_the_peak(self, monkeypatch):
        now = time.time()
        dp = self._fixture(monkeypatch, [
            self._row(now - 9 * 86400, 73.5),
            self._row(now - 8 * 86400, 146.99, perp=73.46),   # double-counted
            self._row(now - 7 * 86400, 75.1),
        ], {"equity_usd": 78.5})
        out = dp.hl_drawdown_from_peak("hl_trading")
        assert out["peak_equity_usd"] == pytest.approx(78.5)
        assert out["drawdown_pct"] == pytest.approx(0.0)
        assert out["samples_skipped_in_position"] == 1

    def test_a_reading_taken_in_position_says_so_and_is_not_compared(self, monkeypatch):
        now = time.time()
        dp = self._fixture(monkeypatch, [self._row(now - 2 * 86400, 100.0)],
                           {"equity_usd": 198.0, "perp_account_value": 99.0})
        out = dp.hl_drawdown_from_peak("hl_trading")
        assert out["reading_in_position"] is True
        assert out["samples"] == 1          # the inflated present is excluded
        assert out["peak_equity_usd"] == pytest.approx(100.0)

    def test_identity_is_the_address_not_the_label(self, monkeypatch):
        now = time.time()
        rows = [self._row(now - 3 * 86400, 200.0, addr="0xother"),
                self._row(now - 2 * 86400, 100.0)]
        dp = self._fixture(monkeypatch, rows, {"equity_usd": 90.0})
        out = dp.hl_drawdown_from_peak("hl_trading")
        assert out["peak_equity_usd"] == pytest.approx(100.0)

    def test_outside_the_lookback_is_excluded(self, monkeypatch):
        now = time.time()
        dp = self._fixture(monkeypatch, [
            self._row(now - 200 * 86400, 500.0),
            self._row(now - 2 * 86400, 100.0),
        ], {"equity_usd": 90.0})
        out = dp.hl_drawdown_from_peak("hl_trading", lookback_days=90)
        assert out["peak_equity_usd"] == pytest.approx(100.0)
