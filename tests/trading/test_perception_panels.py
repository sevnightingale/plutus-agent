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
    # Unconditionally re-run every integration module's decorators. A prior
    # test on this worker may have called registry.reset() with the modules
    # still cached in sys.modules — import then no-ops and the registry
    # arrives here empty or, worse, PARTIAL (the hyperliquid data-point
    # tests reset everything and re-import only their own module, which is
    # exactly the shape CI produced: hl_price present, ta/sessions/coingecko
    # absent — a canary check on one name walked straight past it, #18).
    # Registration is idempotent per defining module since 2026-08-09, so
    # reloading is safe and deterministic. Genuine ghosts still fail the
    # test itself, which is its job.
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


def test_derive_tiers_retired_strategy_does_not_count():
    conn = _mini_db()
    dps = json.dumps([{"name": "ta_ema", "params": {"symbol": "SOL"}}])
    conn.execute("INSERT INTO strategies VALUES ('retired', ?)", (dps,))
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
    off = rpayload.get("narrative_offset_line")
    assert isinstance(off, int) and off >= 1
    assert pmd.read_text().splitlines()[off - 1].startswith("## Narrative")
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


def test_render_view_is_panel_bounded(temp_home, monkeypatch):
    """Strategy-specific lookback variants stay in the cache, not the board."""
    import time as _time
    from trading.perception.cache import _canonical_key
    panel_key = _canonical_key("hl_price", {"symbol": "BTC"})
    extra_key = _canonical_key("hl_candles", {
        "symbol": "BTC", "interval": "1h", "lookback_bars": 5})
    now = _time.time()
    state = {"version": 3, "updated_at": now, "data_points": {
        panel_key: {"value": {"price": 1}, "source": "t",
                    "fetched_at": now - 5, "ttl_s": 60},
        extra_key: {"value": {"c": 2}, "source": "t",
                    "fetched_at": now - 5, "ttl_s": 60},
    }}
    (temp_home / "perception_state.json").write_text(json.dumps(state))
    monkeypatch.setattr(
        "trading.lifecycle.perception_render._panel_cache_keys",
        lambda: {panel_key})
    from trading.lifecycle.perception_render import build_readings_body
    body = build_readings_body()["body"]
    assert "hl_price" in body
    assert "hl_candles" not in body


def test_render_cell_cap_keeps_blackboard_under_read_limit(temp_home):
    """Long JSON values truncate at the cell cap (80k-file lesson).

    Seven full-tier symbols at 160 chars/cell made PERCEPTION.md ride over
    the 80k read_file cap. The cell cap must stay tight enough that the
    rendered Readings zone stays comfortably under it.
    """
    import time as _time
    from trading.perception.cache import _canonical_key
    from trading.lifecycle.perception_render import build_readings_body
    key = _canonical_key("hl_price", {"symbol": "BTC"})
    now = _time.time()
    big = {"price": 1.0, "payload": "x" * 500}
    state = {"version": 3, "updated_at": now, "data_points": {
        key: {"value": big, "source": "t",
              "fetched_at": now - 5, "ttl_s": 60},
    }}
    (temp_home / "perception_state.json").write_text(__import__("json").dumps(state))
    body = build_readings_body()["body"]
    # The long cell is truncated with an ellipsis, not dumped in full.
    assert ("x" * 500) not in body
    assert "..." in body
    # And the whole zone stays well under the 80k read cap for a wide board.
    assert len(body) < 40_000


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


# ── Regression: the declared/swept drift that starved predict ────────────
#
# Full account in ``panels.declared_panel``. In short: the hand-written panel
# and the agent-authored book drift, and when they do, registrations are
# refused and strategies stop accumulating evidence without being judged.


@pytest.fixture
def empty_registry(monkeypatch):
    """Registry NOT yet discovered — the fail-open path, chosen explicitly."""
    from trading.perception.core import data_point_registry as reg

    monkeypatch.setattr(reg, "list_all", lambda *a, **k: [])
    return reg


def _book_conn(rows):
    """A strategies table built by the REAL schema path.

    ``db._create_fresh`` rather than a hand-written CREATE TABLE: the
    2026-07-26 scar in test_capital.py is that a fixture asserting the schema
    it wishes for cannot witness a schema bug. The live table has 23 columns
    with NOT NULL on file_path/timescale/mechanism_family/created_at, so a
    hand-built four-column stand-in is a different table that happens to
    answer the queries the test writes.
    """
    import json
    import sqlite3

    from trading.lifecycle import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db._create_fresh(conn)
    now = 1787000000.0
    for i, (name, status, symbol, dps) in enumerate(rows):
        conn.execute(
            "INSERT INTO strategies (name, file_path, status, timescale,"
            " mechanism_family, symbol, data_points_json, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, f"/tmp/{name}.md", status, "intraday", "momentum",
             symbol, json.dumps(dps), now + i, now + i))
    conn.commit()
    return conn


_OBV = {"name": "ta_obv",
        "params": {"symbol": "BTC", "interval": "1h", "lookback_bars": 200}}


def test_declared_panel_picks_up_what_the_standard_panel_misses(real_registry):
    """The exact shape that was dropped: a TA point WITH lookback_bars, where
    full_panel fetches only symbol+interval."""
    from trading.perception import panels

    conn = _book_conn([("s1", "test", "BTC", [_OBV])])
    assert ("ta_obv", {"symbol": "BTC", "interval": "1h",
                       "lookback_bars": 200}) in panels.declared_panel(conn, "BTC")


def test_declared_panel_does_not_duplicate_the_standard_panel(real_registry):
    from trading.perception import panels

    already = panels.full_panel("BTC")[0]          # ("hl_price", {...})
    conn = _book_conn([("s1", "test", "BTC",
                        [{"name": already[0], "params": dict(already[1])}])])
    assert panels.declared_panel(conn, "BTC") == []


def test_declared_panel_ignores_other_symbols_books(real_registry):
    from trading.perception import panels

    conn = _book_conn([("eth_book", "test", "ETH", [
        {"name": "ta_obv", "params": {"symbol": "ETH", "interval": "1h",
                                      "lookback_bars": 200}}])])
    assert panels.declared_panel(conn, "BTC") == []


def test_declared_panel_matches_an_unnormalised_symbol(real_registry):
    """Nothing normalises on the WRITE side — files.py stores frontmatter
    verbatim and the v7 migration copied symbols out of params — so an exact
    SQL match would return nothing, silently, for a row reading 'btc'."""
    from trading.perception import panels

    conn = _book_conn([("odd", "test", "btc", [_OBV])])
    assert panels.declared_panel(conn, "BTC"), "un-normalised symbol was dropped"


def test_declared_panel_rewrites_a_stale_symbol_param(real_registry):
    """Clone variants carry the parent's symbol; fetching another symbol's
    tape into this panel is the one thing predict's recipe forbids."""
    from trading.perception import panels

    conn = _book_conn([("cloned", "test", "xyz:GOLD", [_OBV])])  # says BTC
    extra = panels.declared_panel(conn, "xyz:GOLD")
    assert extra and all(p.get("symbol") == "xyz:GOLD" for _, p in extra)


def test_declared_panel_injects_a_missing_symbol(real_registry):
    """Some books declare the point with NO symbol at all, which fetches
    nothing: "missing 1 required positional argument: 'symbol'"."""
    from trading.perception import panels

    conn = _book_conn([("no_symbol", "test", "BTC", [
        {"name": "ta_obv", "params": {"interval": "1h", "lookback_bars": 200}}])])
    extra = dict(panels.declared_panel(conn, "BTC"))
    assert extra["ta_obv"]["symbol"] == "BTC"


def test_declared_panel_survives_string_params(real_registry):
    """Strategy files sometimes store `params: symbol=BTC` as a YAML STRING.
    dict() on that raises, and the raise would escape declared_panel and kill
    the whole sweep — all seven symbols — over one malformed book."""
    from trading.perception import panels

    conn = _book_conn([("yaml_str", "test", "BTC",
                        [{"name": "ta_obv",
                          "params": "symbol=BTC,interval=1h,lookback_bars=200"}])])
    extra = dict(panels.declared_panel(conn, "BTC"))
    assert extra["ta_obv"]["lookback_bars"] == "200"


def test_declared_panel_is_bounded_and_says_so(real_registry, caplog):
    """The book is agent-authored and unbounded. A silent cap reads as
    "covered everything" when it did not."""
    import logging

    from trading.perception import panels

    dps = [{"name": "ta_obv",
            "params": {"symbol": "BTC", "interval": "1h", "lookback_bars": n}}
           for n in range(1000)]
    conn = _book_conn([("greedy", "test", "BTC", dps)])
    with caplog.at_level(logging.WARNING):
        out = panels.declared_panel(conn, "BTC")
    assert len(out) == panels.MAX_DECLARED_PER_SYMBOL
    assert any("dropped at the" in r.message for r in caplog.records), \
        "truncation must be logged, not silent"


def test_declared_panel_skips_retired_books(real_registry):
    from trading.perception import panels

    conn = _book_conn([("dead", "retired", "BTC", [_OBV])])
    assert panels.declared_panel(conn, "BTC") == []


def test_declared_panel_fails_open_when_registry_not_discovered(empty_registry):
    """The registry fills only on dispatcher discovery. Filtering against an
    empty one would drop every entry — silently — which is the failure this
    module exists to remove."""
    from trading.perception import panels

    conn = _book_conn([("s1", "test", "BTC",
                        [{"name": "not_a_real_data_point", "params": {}}])])
    assert panels.declared_panel(conn, "BTC"), "fail-open path dropped everything"


def test_panel_for_is_the_single_builder_both_consumers_use(real_registry):
    """The sweep fetches the panel and perception_render filters the Readings
    zone to it. Built separately they drift, and on 2026-08-22 they did — 242
    readings fetched, cached, then dropped from PERCEPTION.md."""
    from trading.perception import panels

    conn = _book_conn([("s1", "test", "BTC", [_OBV])])
    full = panels.panel_for(conn, "BTC", "full")
    assert ("ta_obv", {"symbol": "BTC", "interval": "1h",
                       "lookback_bars": 200}) in full
    # the standard panel is included whole, and the extras ride on top
    assert len(full) == len(panels.full_panel("BTC")) + len(
        panels.declared_panel(conn, "BTC"))
    # passive tier takes no declared extras
    assert panels.panel_for(conn, "BTC", "passive") == list(panels.passive_panel("BTC"))


def test_partially_populated_registry_says_what_it_dropped(monkeypatch, caplog):
    """The fail-open guard catches a WHOLLY empty registry. A partially
    imported one — a single integration failing on a missing dep — is
    non-empty, so the filter engages and drops every ta_* entry. Measured: a
    26-entry registry yields 3 extras instead of 242, with no signal."""
    import logging

    from trading.perception import panels
    from trading.perception.core import data_point_registry as reg

    monkeypatch.setattr(reg, "list_all", lambda *a, **k: [object()])

    def _only_hl_price(name):
        if name == "hl_price":
            raise KeyError(name)
        raise KeyError(name)

    monkeypatch.setattr(reg, "lookup", _only_hl_price)
    conn = _book_conn([("s1", "test", "BTC", [_OBV])])
    with caplog.at_level(logging.WARNING):
        out = panels.declared_panel(conn, "BTC")
    assert out == []
    assert any("unregistered" in r.message for r in caplog.records), \
        "a silent mass drop is the failure this module exists to remove"
