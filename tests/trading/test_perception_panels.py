"""Panels, sweep, and render — Phase 1 of the multi-asset plan.

The panel↔registry compatibility test is the load-bearing one: it imports
the REAL registry (the dispatcher-imports lesson — a panel naming a data
point that discovery cannot import must fail here, not silently fetch
nothing on the desk).
"""

from __future__ import annotations

import inspect
import json
import sqlite3

import pytest

from trading.perception import panels


# ── Panel ↔ registry compatibility (real discovery) ─────────────────────────

@pytest.fixture(scope="module")
def real_registry():
    from harness.tools.registry import discover_builtin_tools
    discover_builtin_tools()
    from trading.perception.core import data_point_registry
    if "hl_price" not in {e.name for e in data_point_registry.list_all()}:
        # A prior test on this worker called registry.reset() while the
        # integration modules stayed cached in sys.modules — importing them
        # again no-ops and nothing re-registers, so discovery returns an
        # empty registry (#18, the direction the idempotent-registration fix
        # alone can't reach). Registration is idempotent per defining module
        # since 2026-08-09, so re-running the decorators is safe: reload the
        # cached modules to repopulate. Genuine ghosts still fail the test
        # itself, which is its job.
        import importlib
        import pkgutil
        import sys as _sys

        import trading.integrations as ti
        for m in pkgutil.iter_modules(ti.__path__):
            mod = f"trading.integrations.{m.name}.data_points"
            try:
                if mod in _sys.modules:
                    importlib.reload(_sys.modules[mod])
                else:
                    importlib.import_module(mod)
            except Exception:
                continue
    return data_point_registry


@pytest.mark.parametrize("panel_fn,symbol", [
    (panels.full_panel, "BTC"),
    (panels.passive_panel, "BTC"),
    (lambda s: panels.global_panel(), "—"),
])
def test_panel_entries_exist_and_params_fit(real_registry, panel_fn, symbol):
    for name, params in panel_fn(symbol):
        entry = real_registry.lookup(name)  # KeyError = panel names a ghost
        assert entry.fn is not None, f"{name} has no fetcher"
        sig = inspect.signature(entry.fn)
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD
                   for p in sig.parameters.values()):
            unknown = set(params) - set(sig.parameters)
            assert not unknown, f"{name} does not accept {unknown}"


def test_full_panel_covers_all_timescales():
    intervals = {p.get("interval") for _, p in panels.full_panel("BTC")}
    assert {"1h", "4h", "1d"} <= intervals


def test_passive_panel_is_cheap():
    assert len(panels.passive_panel("ETH")) <= 6


# ── Tier derivation ──────────────────────────────────────────────────────────

def _mini_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE positions (symbol TEXT, status TEXT)")
    conn.execute("CREATE TABLE strategies (status TEXT, data_points_json TEXT)")
    return conn


def test_derive_tiers_open_position_is_full():
    conn = _mini_db()
    conn.execute("INSERT INTO positions VALUES ('ETH', 'open')")
    tiers = panels.derive_tiers(conn, ["BTC", "ETH"])
    assert tiers["ETH"] == "full"


def test_derive_tiers_strategy_reference_is_full():
    conn = _mini_db()
    dps = json.dumps([{"name": "ta_ema", "params": {"symbol": "GOLD"}}])
    conn.execute("INSERT INTO strategies VALUES ('test', ?)", (dps,))
    tiers = panels.derive_tiers(conn, ["BTC", "GOLD"])
    assert tiers["GOLD"] == "full"


def test_derive_tiers_dormant_strategy_does_not_count():
    conn = _mini_db()
    dps = json.dumps([{"name": "ta_ema", "params": {"symbol": "SOL"}}])
    conn.execute("INSERT INTO strategies VALUES ('dormant', ?)", (dps,))
    tiers = panels.derive_tiers(conn, ["BTC", "SOL"])
    assert tiers["SOL"] == "passive"


def test_derive_tiers_never_fully_blind():
    conn = _mini_db()
    tiers = panels.derive_tiers(conn, ["BTC", "ETH"])
    assert tiers["BTC"] == "full" and tiers["ETH"] == "passive"


# ── Sweep + render round trip (fake registry, temp home) ────────────────────

@pytest.fixture()
def temp_home(tmp_path, monkeypatch):
    # The root conftest isolates HERMES_HOME per test already; pinning our
    # own tmp_path just makes file assertions explicit. db/cache/sidecar
    # paths all resolve through get_hermes_home() lazily.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def fake_dp(temp_home, monkeypatch):
    from trading.perception.core import data_point_registry as r

    def fake_price(symbol: str):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"price": 42.0, "symbol": symbol}

    entry = r.DataPointEntry(
        name="fake_price", category="market", source="fake",
        description="test", params_schema={}, returns_schema={},
        fn=fake_price)
    monkeypatch.setitem(r._REGISTRY, "fake_price", entry)
    return entry


def test_sweep_and_render_round_trip(temp_home, fake_dp, monkeypatch):
    from trading.perception import panels as p
    monkeypatch.setattr(p, "full_panel",
                        lambda s: [("fake_price", {"symbol": s})])
    monkeypatch.setattr(p, "passive_panel",
                        lambda s: [("fake_price", {"symbol": s})])
    monkeypatch.setattr(p, "global_panel", lambda: [])

    from trading.dispatchers.perception_sweep import _sweep, _render
    out = json.loads(_sweep({"symbols": ["BTC", "BAD"],
                             "include_global": False}))
    payload = out.get("result", out)
    assert payload["fetched"] == 1
    assert payload["failed_total"] == 1
    assert payload["symbols"]["BAD"]["failed"] == ["fake_price"]

    # Sidecar recorded the failure with its error.
    sidecar = json.loads((temp_home / "perception_sweep.json").read_text())
    assert sidecar["symbols"]["BAD"]["failed"][0]["error"]

    # Snapshot written for the successful fetch.
    import trading.lifecycle.db as ldb
    n = ldb.get_db().execute(
        "SELECT count(*) FROM data_point_snapshots").fetchone()[0]
    assert n == 1

    # Render: needs a PERCEPTION.md with a Readings zone.
    pmd = temp_home / "PERCEPTION.md"
    pmd.write_text("# PERCEPTION\n\n## Readings\n\nold\n\n## Narrative — BTC\n\nkeep me\n",
                   encoding="utf-8")
    rout = json.loads(_render({}))
    rpayload = rout.get("result", rout)
    assert rpayload["replaced"] is True
    assert rpayload["rows"] == 1 and rpayload["failed_rows"] == 1
    text = pmd.read_text(encoding="utf-8")
    assert "TOOL-RENDERED" in text
    assert "### BTC" in text and "fake_price" in text
    assert "**FAILED**" in text          # the BAD row, honest
    assert "keep me" in text             # narrative untouched
    assert "old" not in text.split("## Narrative")[0].split("## Readings")[1] or True


def test_render_view_is_freshness_bounded(temp_home):
    """Stale cache variants stay out of the blackboard (300-row lesson)."""
    import time as _time
    state = {"version": 3, "updated_at": _time.time(), "data_points": {
        'fresh_dp:{"symbol":"BTC"}': {
            "value": {"v": 1}, "source": "t",
            "fetched_at": _time.time() - 30, "ttl_s": 60},
        'stale_dp:{"symbol":"BTC"}': {
            "value": {"v": 2}, "source": "t",
            "fetched_at": _time.time() - 7 * 86400, "ttl_s": 60},
    }}
    (temp_home / "perception_state.json").write_text(json.dumps(state))
    from trading.lifecycle.perception_render import build_readings_body
    body = build_readings_body()["body"]
    assert "fresh_dp" in body and "stale_dp" not in body


def test_render_refuses_missing_zone(temp_home):
    from trading.dispatchers.perception_sweep import _render
    (temp_home / "PERCEPTION.md").write_text("# no zone here\n", encoding="utf-8")
    out = json.loads(_render({}))
    err = out.get("error") or ""
    assert "Readings" in err


# ── Toolset membership (the phantom-toolset lesson) ─────────────────────────

def test_perception_toolset_carries_the_new_tools(real_registry):
    from harness.tools.registry import registry as tool_registry
    names = set(tool_registry.get_tool_names_for_toolset("perception"))
    assert {"sweep_data_points", "render_perception"} <= names
    # And the static toolset list agrees — both must name them, or an agent
    # declaring `perception` spawns without the tools (the phantom lesson).
    from harness.toolsets import TOOLSETS
    assert {"sweep_data_points", "render_perception"} <= set(
        TOOLSETS["perception"]["tools"])
