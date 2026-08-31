"""register_prediction's event-window gate — gate-tagged calendar DPs are
enforced at registration: has_data=false refuses every declaring book
(a table gap fails closed), in_window=true refuses only declarations
marked event_gate: veto (catalyst books trade the window)."""

import json
import time

import pytest

import trading.dispatchers.register_prediction as RP
from harness.tools.registry import registry as tool_registry
from trading.lifecycle.db import get_db
from trading.perception.core.data_point_registry import register_data_point
from trading.strategies import files as strat_files

# One fake gate-tagged calendar DP; each test steers READING.
READING = {}


@register_data_point(
    name="test_gate_calendar", category="macro", source="test",
    description="test gate", params_schema={}, returns_schema={},
    tags=("calendar", "gate"), numeric_path="days_to_next",
)
def _fake_calendar():  # pragma: no cover — replaced by the fetch mock
    return dict(READING)


def _write_strategy(name, event_gate=None):
    dp = {"name": "test_gate_calendar", "params": {}, "weight": 1.0}
    if event_gate:
        dp["event_gate"] = event_gate
    s = strat_files.Strategy(
        name=name, status="test", timescale="intraday",
        mechanism_family="event",
        file_path=strat_files.strategies_dir() / f"{name}.md",
        regime_applicability={"intraday": {"direction": ["ranging"],
                                           "volatility": ["normal"]}},
        data_points=[dp], created="2026-08-31",
        body_md=("# Hypothesis\nx\n# Mechanism\nx\n# Trigger\nx\n"
                 "# Invalidation\nx\n"),
    )
    strat_files.strategies_dir().mkdir(parents=True, exist_ok=True)
    s.file_path.write_text(strat_files.render_strategy(s), encoding="utf-8")
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO strategies (name,file_path,status,timescale,"
        "mechanism_family,created_at,updated_at) VALUES (?,?,?,?,?,0,0)",
        (name, str(s.file_path), "test", "intraday", "event"))
    conn.commit()


def _register(name):
    return json.loads(tool_registry.get_entry("register_prediction").handler({
        "claim": "z", "symbol": "BTC", "horizon_hours": 12,
        "near_edge_pct": 2.0, "far_edge_pct": 4.0, "conviction": 0.7,
        "kind": "strategy", "strategy_name": name,
    }))


@pytest.fixture(autouse=True)
def _plumb(monkeypatch):
    monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100_000.0)
    import trading.perception.fetch_core as fc
    monkeypatch.setattr(
        fc, "fetch_and_snapshot",
        lambda name, params=None, **kw: dict(READING) if name == "test_gate_calendar"
        else {"ok": False, "name": name, "error": "unexpected fetch"})
    READING.clear()


class TestTableGapFailsClosed:
    def test_has_data_false_refuses_every_declaring_book(self):
        _write_strategy("gap-book")
        READING.update({"ok": True, "value": {"has_data": False}})
        res = _register("gap-book")
        assert "fails closed" in res.get("error", "")

    def test_unreadable_gate_refuses(self):
        _write_strategy("dead-gate-book")
        READING.update({"ok": False, "error": "boom"})
        res = _register("dead-gate-book")
        assert "unreadable" in res.get("error", "")


class TestVetoVsCatalyst:
    def test_veto_refuses_in_window(self):
        _write_strategy("veto-book", event_gate="veto")
        READING.update({"ok": True, "value": {
            "has_data": True, "in_window": True, "kind": "earnings",
            "next_event": "2026-11-17", "days_to_next": 3.0}})
        res = _register("veto-book")
        assert "VETO" in res.get("error", "")

    def test_catalyst_registers_through_the_window(self):
        """Unmarked declarations are catalyst books — the window is their
        setup; the gate must not block them."""
        _write_strategy("catalyst-book")
        READING.update({"ok": True, "value": {
            "has_data": True, "in_window": True, "kind": "lockup",
            "next_event": "2026-09-09", "days_to_next": 9.0}})
        res = _register("catalyst-book")
        assert res["ok"], res

    def test_veto_passes_outside_the_window(self):
        _write_strategy("veto-quiet-book", event_gate="veto")
        READING.update({"ok": True, "value": {
            "has_data": True, "in_window": False, "days_to_next": 40.0}})
        res = _register("veto-quiet-book")
        assert res["ok"], res


class TestDeclarationValidation:
    def test_event_gate_vocabulary_is_closed(self, tmp_path):
        s = strat_files.Strategy(
            name="bad-gate", status="test", timescale="intraday",
            mechanism_family="event", file_path=tmp_path / "bad-gate.md",
            regime_applicability={"intraday": {"direction": ["ranging"],
                                               "volatility": ["normal"]}},
            data_points=[{"name": "test_gate_calendar", "params": {},
                          "weight": 1.0, "event_gate": "sometimes"}],
            created="2026-08-31", body_md="# Hypothesis\nx\n# Mechanism\nx\n"
                                          "# Trigger\nx\n# Invalidation\nx\n",
        )
        problems = strat_files.validate_strategy(s)
        assert any("event_gate" in p for p in problems)
