"""Capital reconciliation — the table that had a schema and no writer.

capital_movements existed from the beginning with zero callers; the live
runtime held 0 rows while equity moved $23.99 -> $75.12, so every P&L figure
the desk could state was gross of unknown deposits.
"""

import sqlite3

import pytest

from trading.lifecycle import capital, write


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE capital_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_name TEXT,
            ts REAL NOT NULL,
            from_account TEXT,
            to_account TEXT,
            token TEXT NOT NULL,
            amount_token REAL NOT NULL,
            amount_usd_at_time REAL,
            movement_type TEXT NOT NULL,
            tx_hash TEXT,
            note TEXT
        );
        CREATE UNIQUE INDEX ux_capital_movements_tx
            ON capital_movements(tx_hash);
        """
    )
    return c


# The two real movements on the live account, as the venue reports them.
_LEDGER = [
    {"ts": 1777989506.86, "tx_hash": "0x7b3f66bd", "movement_type": "send",
     "token": "USDC", "amount_token": 23.989376, "amount_usd_at_time": 23.989376,
     "from_account": "0x9bda", "to_account": "0x9829", "note": None},
    {"ts": 1783005744.5, "tx_hash": "0x5a6ac41f", "movement_type": "send",
     "token": "USDC", "amount_token": 58.986712, "amount_usd_at_time": 58.986712,
     "from_account": "0x9bda", "to_account": "0x9829", "note": None},
]


@pytest.fixture()
def ledger(monkeypatch):
    calls = {"n": 0}

    def _fake(account_name="hl_trading", start_ms=0, **_kw):
        calls["n"] += 1
        return list(_LEDGER)

    monkeypatch.setattr(
        "trading.integrations.hyperliquid.venue.hl_capital_ledger", _fake)
    return calls


class TestReconcile:
    def test_backfills_history_on_first_run(self, ledger):
        c = _conn()
        out = capital.reconcile_capital_movements(c)
        assert out["ok"] and out["inserted"] == 2 and out["seen"] == 2
        assert out["gross_deposits_usd"] == pytest.approx(82.976088)
        assert out["net_deposits_usd"] == pytest.approx(82.976088)

    def test_is_idempotent(self, ledger):
        c = _conn()
        capital.reconcile_capital_movements(c)
        second = capital.reconcile_capital_movements(c)
        assert second["inserted"] == 0, "a second pass must not duplicate history"
        assert second["movement_count"] == 2

    def test_new_movement_is_picked_up(self, ledger, monkeypatch):
        c = _conn()
        capital.reconcile_capital_movements(c)
        monkeypatch.setattr(
            "trading.integrations.hyperliquid.venue.hl_capital_ledger",
            lambda *a, **k: _LEDGER + [{
                "ts": 1785000000.0, "tx_hash": "0xdeadbeef",
                "movement_type": "withdraw", "token": "USDC",
                "amount_token": -10.0, "amount_usd_at_time": -10.0,
                "from_account": "0x9829", "to_account": "0xelsewhere",
                "note": None}])
        out = capital.reconcile_capital_movements(c)
        assert out["inserted"] == 1
        assert out["gross_withdrawals_usd"] == pytest.approx(-10.0)
        assert out["net_deposits_usd"] == pytest.approx(72.976088)

    def test_venue_failure_is_honest_not_zero(self, monkeypatch):
        """A silent zero would read downstream as 'no deposits ever' and make
        the P&L wrong in the flattering direction."""
        def _boom(*a, **k):
            raise RuntimeError("HL unreachable")
        monkeypatch.setattr(
            "trading.integrations.hyperliquid.venue.hl_capital_ledger", _boom)
        out = capital.reconcile_capital_movements(_conn())
        assert out["ok"] is False
        assert "HL unreachable" in out["error"]
        assert out["inserted"] == 0

    def test_unparsed_rows_counted_not_written(self, monkeypatch):
        monkeypatch.setattr(
            "trading.integrations.hyperliquid.venue.hl_capital_ledger",
            lambda *a, **k: [{"ts": 1.0, "tx_hash": "0xbad",
                              "movement_type": "mystery", "token": "USDC",
                              "amount_token": None, "amount_usd_at_time": None,
                              "from_account": None, "to_account": None,
                              "note": "UNPARSED"}])
        out = capital.reconcile_capital_movements(_conn())
        assert out["ok"] and out["unparsed"] == 1 and out["inserted"] == 0


class TestLifetimePnl:
    def test_measures_equity_against_capital(self, ledger):
        c = _conn()
        capital.reconcile_capital_movements(c)
        out = capital.lifetime_pnl(c, equity_usd=75.121746)
        assert out["ok"]
        assert out["pnl_usd"] == pytest.approx(-7.854342)
        assert out["pnl_pct_of_capital"] == pytest.approx(-9.4658, abs=1e-3)

    def test_no_capital_recorded_yields_no_percentage(self):
        out = capital.lifetime_pnl(_conn(), equity_usd=10.0)
        assert out["pnl_pct_of_capital"] is None

    def test_equity_read_failure_is_honest(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("no venue")
        monkeypatch.setattr(
            "trading.integrations.hyperliquid.venue.hl_account_state", _boom)
        out = capital.lifetime_pnl(_conn())
        assert out["ok"] is False and "no venue" in out["error"]


class TestWriter:
    def test_duplicate_tx_hash_ignored(self):
        c = _conn()
        first = write.record_capital_movement(
            c, ts=1.0, token="USDC", amount_token=5.0,
            movement_type="send", tx_hash="0xabc")
        second = write.record_capital_movement(
            c, ts=1.0, token="USDC", amount_token=5.0,
            movement_type="send", tx_hash="0xabc")
        assert first is not None and second is None
        assert c.execute("SELECT COUNT(*) FROM capital_movements").fetchone()[0] == 1

    def test_hand_recorded_movements_without_hash_are_allowed(self):
        c = _conn()
        a = write.record_capital_movement(
            c, ts=1.0, token="USDC", amount_token=5.0, movement_type="send")
        b = write.record_capital_movement(
            c, ts=2.0, token="USDC", amount_token=6.0, movement_type="send")
        assert a is not None and b is not None


class TestTool:
    def test_registered_under_resolution(self):
        from harness.tools import registry as reg
        import trading.dispatchers.capital  # noqa: F401
        assert reg.registry.get_toolset_for_tool("capital_reconcile") == "resolution"
