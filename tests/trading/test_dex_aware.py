"""Dex-aware read-only data — Step B of the multi-asset round.

Builder-dex symbols ("xyz:GOLD") route through two seams: get_info()'s
perp_dexs construction (SDK name maps) and dex_of() routing on the fetchers
that take a dex parameter or need the per-dex meta request.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading.integrations.hyperliquid import _client


class TestDexOf:
    def test_main_dex_symbols(self):
        assert _client.dex_of("BTC") == ""
        assert _client.dex_of("ETH") == ""

    def test_builder_dex_symbols(self):
        assert _client.dex_of("xyz:GOLD") == "xyz"
        assert _client.dex_of("xyz:SP500") == "xyz"
        assert _client.dex_of("flx:SOMETHING") == "flx"


class TestConfiguredDexs:
    def test_reads_trading_perp_dexs(self, monkeypatch):
        import harness.cli.config as cfg
        monkeypatch.setattr(cfg, "load_config",
                            lambda: {"trading": {"perp_dexs": ["xyz", " flx "]}})
        assert _client._configured_perp_dexs() == ["xyz", "flx"]

    def test_empty_config_means_no_dexs(self, monkeypatch):
        import harness.cli.config as cfg
        monkeypatch.setattr(cfg, "load_config", lambda: {})
        assert _client._configured_perp_dexs() == []


class TestRouting:
    def test_hl_price_routes_dex(self):
        from trading.integrations.hyperliquid.data_points import hl_price
        fake = MagicMock()
        fake.all_mids.return_value = {"xyz:GOLD": "4348.25"}
        with patch.object(_client, "get_info", return_value=fake), \
             patch("trading.integrations.hyperliquid.data_points.get_info",
                   return_value=fake):
            out = hl_price("xyz:GOLD")
        fake.all_mids.assert_called_once_with(dex="xyz")
        assert out["price"] == 4348.25

    def test_hl_price_main_dex_unchanged(self):
        from trading.integrations.hyperliquid.data_points import hl_price
        fake = MagicMock()
        fake.all_mids.return_value = {"BTC": "64900.0"}
        with patch.object(_client, "get_info", return_value=fake), \
             patch("trading.integrations.hyperliquid.data_points.get_info",
                   return_value=fake):
            out = hl_price("BTC")
        fake.all_mids.assert_called_once_with(dex="")
        assert out["price"] == 64900.0

    def test_funding_routes_via_meta_and_ctxs(self):
        from trading.integrations.hyperliquid.data_points import hl_funding_and_oi
        payload = [
            {"universe": [{"name": "xyz:GOLD"}]},
            [{"funding": "0.00001", "premium": "0.0001",
              "markPx": "4348.1", "openInterest": "76000"}],
        ]
        with patch.object(_client, "meta_and_ctxs",
                          return_value=payload) as m:
            out = hl_funding_and_oi("xyz:GOLD")
        m.assert_called_once_with("xyz")
        assert out["funding"] == pytest.approx(1e-05)
        assert out["open_interest"] == 76000.0

    def test_meta_and_ctxs_raw_post_for_builder_dex(self):
        fake = MagicMock()
        with patch.object(_client, "get_info", return_value=fake):
            _client.meta_and_ctxs("xyz")
            fake.post.assert_called_once_with(
                "/info", {"type": "metaAndAssetCtxs", "dex": "xyz"})
            fake.meta_and_asset_ctxs.assert_not_called()
            fake.reset_mock()
            _client.meta_and_ctxs("")
            fake.meta_and_asset_ctxs.assert_called_once()
            fake.post.assert_not_called()


class TestPanels:
    def test_poly_ladder_gated_to_crypto(self):
        from trading.perception import panels
        btc_names = [n for n, _ in panels.full_panel("BTC")]
        gold_names = [n for n, _ in panels.full_panel("xyz:GOLD")]
        assert "poly_price_ladder" in btc_names
        assert "poly_price_ladder" not in gold_names

    def test_normalize_symbol_preserves_dex_case(self):
        from trading.perception.panels import normalize_symbol
        # A blanket .upper() would mangle the dex half — the venue knows
        # "xyz:GOLD", not "XYZ:GOLD".
        assert normalize_symbol("xyz:gold") == "xyz:GOLD"
        assert normalize_symbol("XYZ:GOLD") == "xyz:GOLD"
        assert normalize_symbol(" btc ") == "BTC"


class TestMergedAccountReads:
    """Account-WIDE reads must span every configured dex.

    Hyperliquid's ``dex`` parameter defaults to the FIRST perp dex, not all of
    them, so a bare ``user_state`` / ``frontend_open_orders`` / ``all_mids``
    answers a narrower question than the caller asked. Measured live
    2026-08-25: main-dex ``all_mids()`` returned 950 symbols, none of them
    ``xyz:``; the ``xyz`` dex held 116 including BRENTOIL.
    """

    @staticmethod
    def _two_dex(monkeypatch):
        monkeypatch.setattr(_client, "_configured_perp_dexs", lambda: ["xyz"])

    @staticmethod
    def _no_dex(monkeypatch):
        monkeypatch.setattr(_client, "_configured_perp_dexs", lambda: [])

    def test_merged_all_mids_spans_dexs(self, monkeypatch):
        self._two_dex(monkeypatch)
        fake = MagicMock()
        fake.all_mids.side_effect = [
            {"BTC": "64900.0"}, {"xyz:BRENTOIL": "87.88"}]
        with patch.object(_client, "get_info", return_value=fake):
            mids = _client.merged_all_mids()
        assert mids == {"BTC": "64900.0", "xyz:BRENTOIL": "87.88"}
        assert [c.kwargs["dex"] for c in fake.all_mids.call_args_list] == ["", "xyz"]

    def test_no_configured_dex_makes_one_call(self, monkeypatch):
        self._no_dex(monkeypatch)
        fake = MagicMock()
        fake.all_mids.return_value = {"BTC": "64900.0"}
        with patch.object(_client, "get_info", return_value=fake):
            mids = _client.merged_all_mids()
        assert mids == {"BTC": "64900.0"}
        fake.all_mids.assert_called_once_with(dex="")

    def test_merged_open_orders_concatenates(self, monkeypatch):
        self._two_dex(monkeypatch)
        fake = MagicMock()
        fake.frontend_open_orders.side_effect = [
            [{"coin": "BTC", "oid": 1}],
            [{"coin": "xyz:GOLD", "oid": 2, "triggerPx": "4654.1"}],
        ]
        with patch.object(_client, "get_info", return_value=fake):
            orders = _client.merged_open_orders("0xabc")
        assert [o["oid"] for o in orders] == [1, 2]
        assert [c.args[1] for c in fake.frontend_open_orders.call_args_list] == ["", "xyz"]

    def test_merged_user_state_sums_account_value(self, monkeypatch):
        self._two_dex(monkeypatch)
        fake = MagicMock()
        fake.user_state.side_effect = [
            {"marginSummary": {"accountValue": "10.0"},
             "assetPositions": [{"position": {"coin": "BTC", "szi": "0.1"}}]},
            {"marginSummary": {"accountValue": "68.5"},
             "assetPositions": [{"position": {"coin": "xyz:GOLD", "szi": "0.01"}}]},
        ]
        with patch.object(_client, "get_info", return_value=fake):
            state = _client.merged_user_state("0xabc")
        # Each perp dex keeps its own margin — the sum is the account.
        assert float(state["marginSummary"]["accountValue"]) == 78.5
        assert state["account_value_by_dex"] == {"main": 10.0, "xyz": 68.5}
        assert [p["position"]["coin"] for p in state["assetPositions"]] == [
            "BTC", "xyz:GOLD"]

    def test_dex_failure_raises_rather_than_half_reading(self, monkeypatch):
        self._two_dex(monkeypatch)
        fake = MagicMock()
        fake.user_state.side_effect = [
            {"marginSummary": {"accountValue": "10.0"}, "assetPositions": []},
            RuntimeError("dex read failed"),
        ]
        # A half-read account is the failure mode that started all this: a
        # partial map reads as a real absence everywhere downstream.
        with patch.object(_client, "get_info", return_value=fake):
            with pytest.raises(RuntimeError):
                _client.merged_user_state("0xabc")
