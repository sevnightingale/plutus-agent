"""Issue 2 — the Live State writer (replace_zone + write_live_state + tool)."""

import sqlite3

import pytest

from trading.lifecycle import live_state as LS


_PLUTUS = """# PLUTUS

## Doctrine

North star. (operator-owned — must NOT change)

## Live State

<!-- TOOL-REWRITTEN ONLY. Do not edit by hand. -->
- equity_usd: (not yet snapshotted)
- snapshot_at: —
- regime: see REGIME.md
- open_position: none
- strategies: 0 active / 0 test / 0 dormant / 0 retired

## Lessons

- L1. (reflect-owned — must NOT change)
"""


class TestReplaceZone:
    def test_surgical_replace_preserves_other_zones(self, tmp_path):
        p = tmp_path / "PLUTUS.md"
        p.write_text(_PLUTUS)
        ok = LS.replace_zone(p, "live-state", f"{LS._MARKER}\n- equity_usd: $17.18")
        out = p.read_text()
        assert ok
        assert "operator-owned — must NOT change" in out      # Doctrine intact
        assert "reflect-owned — must NOT change" in out        # Lessons intact
        assert "$17.18" in out
        assert out.count("## ") == 3                           # no zones added/lost

    def test_round_trips_through_read_zone(self, tmp_path):
        from harness.spawn import _read_zone
        p = tmp_path / "PLUTUS.md"
        p.write_text(_PLUTUS)
        LS.replace_zone(p, "live-state", f"{LS._MARKER}\n- equity_usd: $42.00")
        assert "$42.00" in _read_zone(p, "live-state")

    def test_missing_file_or_zone_returns_false(self, tmp_path):
        assert LS.replace_zone(tmp_path / "nope.md", "live-state", "x") is False
        p = tmp_path / "PLUTUS.md"
        p.write_text("# PLUTUS\n\n## Doctrine\n\nx\n")
        assert LS.replace_zone(p, "live-state", "x") is False


def _strategies_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE strategies(status TEXT);"
        "INSERT INTO strategies VALUES "
        "('active'),('test'),('test'),('test'),('dormant'),('retired'),('retired');")
    return c


class TestBuildBody:
    def test_flat_with_equity(self, monkeypatch):
        monkeypatch.setattr(
            "trading.integrations.hyperliquid.venue.hl_account_state",
            lambda *a, **k: {"equity_usd": 17.18})
        monkeypatch.setattr("trading.lifecycle.queries.open_position", lambda conn: None)
        body = LS.build_live_state_body(_strategies_conn())
        assert "- equity_usd: $17.18" in body
        assert "- open_position: none" in body
        assert "- strategies: 1 active / 3 test / 1 dormant / 2 retired" in body
        assert LS._MARKER in body

    def test_equity_failure_is_honest_not_stale(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("no creds")
        monkeypatch.setattr("trading.integrations.hyperliquid.venue.hl_account_state", _boom)
        monkeypatch.setattr("trading.lifecycle.queries.open_position", lambda conn: None)
        body = LS.build_live_state_body(_strategies_conn())
        assert "- equity_usd: unavailable (RuntimeError)" in body
        assert "equity read failed" in body

    def test_open_position_summary(self, monkeypatch):
        monkeypatch.setattr(
            "trading.integrations.hyperliquid.venue.hl_account_state",
            lambda *a, **k: {"equity_usd": 100.0})
        monkeypatch.setattr("trading.lifecycle.queries.open_position", lambda conn: {
            "symbol": "BTC", "side": "long", "size": 0.001,
            "thesis": {"strategy_name": "squeeze-breakout", "sl_price": 99000.0},
            "last_evaluation": {"conviction": 0.62}})
        body = LS.build_live_state_body(_strategies_conn())
        line = [l for l in body.splitlines() if l.startswith("- open_position:")][0]
        assert "BTC long" in line and "strat=squeeze-breakout" in line
        assert "sl=99000.0" in line and "conv=0.62" in line


class TestWriteLiveState:
    def test_writes_zone_and_reports_ok(self, tmp_path, monkeypatch):
        p = tmp_path / "PLUTUS.md"
        p.write_text(_PLUTUS)
        monkeypatch.setattr(LS, "build_live_state_body",
                            lambda conn: f"{LS._MARKER}\n- equity_usd: $5.00")
        res = LS.write_live_state(conn=object(), path=p)
        assert res["ok"] and res["error"] is None
        assert "$5.00" in p.read_text()

    def test_missing_zone_reports_error_not_silent_create(self, tmp_path, monkeypatch):
        p = tmp_path / "PLUTUS.md"
        p.write_text("# PLUTUS\n\n## Doctrine\n\nx\n")
        monkeypatch.setattr(LS, "build_live_state_body", lambda conn: "body")
        res = LS.write_live_state(conn=object(), path=p)
        assert res["ok"] is False
        assert "Live State" in res["error"]


class TestTool:
    def test_sync_live_state_registered_under_resolution(self):
        import harness.tools.registry as R
        R.discover_builtin_tools()
        assert "sync_live_state" in R.registry.get_all_tool_names()
        assert R.registry.get_toolset_for_tool("sync_live_state") == "resolution"
