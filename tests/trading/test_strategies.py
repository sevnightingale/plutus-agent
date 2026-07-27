"""Strategy file format, validation, loader, mirror sync."""

import pytest

from trading.lifecycle.db import get_db
from trading.strategies import loader
from trading.strategies.files import (
    Strategy,
    parse_strategy,
    render_strategy,
    resolve_dp_key,
    validate_strategy,
)

BODY = """
# Hypothesis
After a deeply negative funding flush while price holds support, BTC mean-reverts
upward within 24h.

# Mechanism
Over-levered shorts pay to hold; forced covering supplies the bid. The other side
is late momentum shorts chasing the flush.

# Trigger
hl_funding < -0.01%/h AND price within 1 ATR of support.

# Invalidation template
Support breaks on rising CVD sell pressure — the flush was distribution, not capitulation.
"""


def _strategy(tmp_path, **over):
    fields = dict(
        name="funding-flush-reversal",
        status="test",
        timescale="intraday",
        mechanism_family="flow",
        file_path=tmp_path / "funding-flush-reversal.md",
        regime_applicability={"direction": ["ranging"], "volatility": ["elevated"]},
        data_points=[
            {"name": "hl_funding", "params": {"symbol": "BTC"}, "weight": 0.4},
            {"name": "hl_cvd", "params": {"symbol": "BTC"}, "weight": 0.3},
            {"name": "ta_atr", "params": {"symbol": "BTC"}, "weight": 0.3},
        ],
        created="2026-06-15",
        body_md=BODY,
    )
    fields.update(over)
    return Strategy(**fields)


@pytest.fixture()
def conn(tmp_path):
    c = get_db(tmp_path / "lifecycle.db")
    yield c
    c.close()


class TestFileFormat:
    def test_render_parse_round_trip(self, tmp_path):
        s = _strategy(tmp_path)
        s.file_path.write_text(render_strategy(s), encoding="utf-8")
        back = parse_strategy(s.file_path)
        assert back.name == s.name
        assert back.status == "test"
        assert back.weights == {
            "hl_funding(symbol=BTC)": 0.4,
            "hl_cvd(symbol=BTC)": 0.3,
            "ta_atr(symbol=BTC)": 0.3,
        }
        assert "forced covering" in back.body_section("Mechanism")

    def test_validation_catches_problems(self, tmp_path):
        bad = _strategy(
            tmp_path, status="paused", timescale="longer",
            data_points=[{"name": "hl_funding", "weight": 1.4}],
            body_md="# Hypothesis\nx\n",
        )
        problems = validate_strategy(bad)
        joined = "\n".join(problems)
        assert "status" in joined
        assert "timescale" in joined
        assert "1.4" in joined
        assert "Mechanism" in joined

    def test_variant_requires_stated_tweak(self, tmp_path):
        v = _strategy(tmp_path, parent_strategy="funding-flush-reversal")
        assert any("tweak" in p for p in validate_strategy(v))

    def test_unregistered_dp_needs_self_extension_hook(self, tmp_path):
        s = _strategy(tmp_path)
        problems = validate_strategy(s, known_data_points={"hl_funding", "hl_cvd"})
        assert any("ta_atr" in p for p in problems)
        s.missing_data_points = ["ta_atr"]
        assert validate_strategy(
            s, known_data_points={"hl_funding", "hl_cvd"}) == []


class TestLoader:
    def test_status_gates_context(self, tmp_path, conn):
        for name, status in [
            ("alpha-live", "active"), ("beta-test", "test"),
            ("gamma-dorm", "dormant"), ("delta-dead", "retired"),
        ]:
            s = _strategy(tmp_path, name=name, status=status,
                          file_path=tmp_path / f"{name}.md")
            loader.write_strategy(s, conn)

        live = loader.load_strategies(base_dir=tmp_path)
        assert sorted(s.name for s in live) == ["alpha-live", "beta-test"]

        block = loader.strategy_context_block(base_dir=tmp_path)
        assert "alpha-live" in block and "beta-test" in block
        assert "gamma-dorm" not in block and "delta-dead" not in block

    def test_empty_book_says_no_trades(self, tmp_path):
        block = loader.strategy_context_block(base_dir=tmp_path)
        assert "NO" in block and "trades" in block

    def test_write_syncs_mirror(self, tmp_path, conn):
        loader.write_strategy(_strategy(tmp_path), conn)
        row = conn.execute(
            "SELECT status, timescale, mechanism_family, hypothesis_md "
            "FROM strategies WHERE name='funding-flush-reversal'").fetchone()
        assert row["status"] == "test"
        assert row["timescale"] == "intraday"
        assert "mean-reverts" in row["hypothesis_md"]

    def test_set_status_updates_file_and_mirror(self, tmp_path, conn):
        loader.write_strategy(_strategy(tmp_path), conn)
        loader.set_status("funding-flush-reversal", "retired", conn,
                          reason="failed checkpoint 4/10", base_dir=tmp_path)
        back = parse_strategy(tmp_path / "funding-flush-reversal.md")
        assert back.status == "retired"
        assert back.retirement_reason == "failed checkpoint 4/10"
        row = conn.execute(
            "SELECT status, retirement_reason FROM strategies "
            "WHERE name='funding-flush-reversal'").fetchone()
        assert row["status"] == "retired"
        assert "checkpoint" in row["retirement_reason"]

    def test_invalid_write_refused_and_nothing_lands(self, tmp_path, conn):
        bad = _strategy(tmp_path, mechanism_family="vibes")
        with pytest.raises(ValueError, match="refused"):
            loader.write_strategy(bad, conn)
        assert not bad.file_path.exists()
        assert conn.execute("SELECT COUNT(*) c FROM strategies").fetchone()["c"] == 0


class TestResolveDpKey:
    DPS = [
        {"name": "ta_vortex", "params": {"interval": "1h", "symbol": "BTC"}, "weight": 0.3},
        {"name": "ta_vortex", "params": {"interval": "4h", "symbol": "BTC"}, "weight": 0.3},
        {"name": "hl_cvd", "params": {"interval": "1h", "symbol": "BTC"}, "weight": 0.4},
    ]

    def test_exact_canonical(self):
        assert resolve_dp_key(self.DPS, "hl_cvd(interval=1h,symbol=BTC)") == \
            "hl_cvd(interval=1h,symbol=BTC)"

    def test_bare_name_unique(self):
        assert resolve_dp_key(self.DPS, "hl_cvd") == "hl_cvd(interval=1h,symbol=BTC)"

    def test_bare_name_ambiguous_refused(self):
        assert resolve_dp_key(self.DPS, "ta_vortex") is None

    def test_paren_interval_shorthand(self):
        assert resolve_dp_key(self.DPS, "ta_vortex(4h)") == \
            "ta_vortex(interval=4h,symbol=BTC)"

    def test_paren_kv_subset(self):
        assert resolve_dp_key(self.DPS, "ta_vortex(interval=1h)") == \
            "ta_vortex(interval=1h,symbol=BTC)"

    def test_suffix_shorthand(self):
        assert resolve_dp_key(self.DPS, "ta_vortex_4h") == \
            "ta_vortex(interval=4h,symbol=BTC)"

    def test_unknown_name(self):
        assert resolve_dp_key(self.DPS, "ta_rsi") is None

    def test_hint_matching_nothing(self):
        assert resolve_dp_key(self.DPS, "ta_vortex(interval=2h)") is None

    def test_paramless_declaration(self):
        dps = [{"name": "macro_vix", "weight": 1.0}]
        assert resolve_dp_key(dps, "macro_vix") == "macro_vix"

    def test_empty_key(self):
        assert resolve_dp_key(self.DPS, "") is None


class TestUpdateWeightsDispatcher:
    """The silent-no-op fix: bare keys resolve against the declaration;
    unresolvable keys refuse the whole update loudly."""

    def _tool(self):
        import json as _json

        import trading.dispatchers.strategy_tools  # noqa: F401 — registers
        from harness.tools.registry import registry as tool_registry
        entry = tool_registry.get_entry("strategy_update_weights")
        return lambda args: _json.loads(entry.handler(args))

    def _seed(self, conn):
        from trading.strategies.files import strategies_dir
        s = _strategy(strategies_dir())
        loader.write_strategy(s, conn)
        return s

    def test_bare_keys_resolve_and_apply(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        self._seed(conn)
        call = self._tool()
        res = call({"name": "funding-flush-reversal",
                    "dp_performance": {"hl_funding": 1.0, "hl_cvd": -1.0}})
        assert res["ok"], res
        # alpha 0.05: hl_cvd 0.3 → 0.25 (bare key resolved, decrease applied);
        # hl_funding stays 0.4 (growth past the 0.30 cap is clamped by design)
        assert res["weights"]["hl_cvd(symbol=BTC)"] == pytest.approx(0.25)
        assert res["weights"]["hl_funding(symbol=BTC)"] == pytest.approx(0.4)

    def test_unknown_key_refuses_whole_update(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        self._seed(conn)
        call = self._tool()
        res = call({"name": "funding-flush-reversal",
                    "dp_performance": {"hl_funding": 1.0, "ta_nope": 0.5}})
        assert "error" in res
        assert "ta_nope" in res["error"]
        assert "hl_funding(symbol=BTC)" in res["error"]  # declared keys listed
        # nothing changed on disk
        from trading.strategies.files import strategies_dir
        back = parse_strategy(strategies_dir() / "funding-flush-reversal.md")
        assert back.weights["hl_funding(symbol=BTC)"] == pytest.approx(0.4)


class TestNormalizerDeclaration:
    def test_valid_spec_writes(self, tmp_path, conn):
        s = _strategy(tmp_path)
        s.data_points[0]["normalizer"] = {"name": "linear_band",
                                          "params": {"lo": -0.01, "hi": -0.05}}
        loader.write_strategy(s, conn)
        back = parse_strategy(s.file_path)
        assert back.data_points[0]["normalizer"]["name"] == "linear_band"

    def test_bad_spec_refused_at_write(self, tmp_path, conn):
        s = _strategy(tmp_path)
        s.data_points[0]["normalizer"] = {"name": "linear_band",
                                          "params": {"lo": 1, "hi": 1}}
        with pytest.raises(ValueError, match="invalid config"):
            loader.write_strategy(s, conn)
        s.data_points[0]["normalizer"] = {"name": "not_a_normalizer"}
        with pytest.raises(ValueError, match="unknown normalizer"):
            loader.write_strategy(s, conn)


class TestOneCellDeclaration:
    """regime_applicability declares exactly one cell — refused in the writer.

    The (timescale x regime) cap lived as prose in the reflect brief since the
    rebuild and was ignored for as long, so this rule sits where it can say
    no. A set-valued declaration produces one book averaging several different
    trades: ema20-pivot-swing blended to -0.004 and met the retirement bar
    while four of its five cells were positive.
    """

    def _tool(self):
        import json as _json

        import trading.dispatchers.strategy_tools  # noqa: F401 — registers
        from harness.tools.registry import registry as tool_registry
        entry = tool_registry.get_entry("strategy_upsert")
        return lambda args: _json.loads(entry.handler(args))

    def _args(self, regime):
        # The data point is declared via the self-extension hook rather than
        # named from the registry: _REGISTRY is populated by import-time
        # decorators, so which entries exist depends on what else the xdist
        # worker imported first. This test is about the cell declaration and
        # must not inherit that coupling.
        return {
            "name": "one-cell-probe", "timescale": "swing",
            "mechanism_family": "flow", "regime_applicability": regime,
            "data_points": [{"name": "not_yet_sourced_dp",
                             "params": {"symbol": "BTC"}, "weight": 0.4}],
            "missing_data_points": ["not_yet_sourced_dp"],
            "body": ("# Hypothesis\nx\n\n# Mechanism\ny\n\n"
                     "# Trigger\nz\n\n# Invalidation\nw\n"),
        }

    def test_single_cell_is_accepted(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        res = self._tool()(self._args(
            {"swing": {"direction": ["ranging"], "volatility": ["compressed"]}}))
        assert res.get("ok"), res

    def test_a_set_on_any_axis_is_refused(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        res = self._tool()(self._args(
            {"swing": {"direction": ["ranging", "trending-up"],
                       "volatility": ["compressed"]}}))
        assert not res.get("ok")
        assert "one cell" in res["error"].lower()
        assert "direction" in res["error"]

    def test_the_refusal_names_every_offending_axis(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        res = self._tool()(self._args(
            {"swing": {"direction": ["ranging", "trending-up"],
                       "volatility": ["compressed", "normal"]}}))
        assert not res.get("ok")
        assert "direction" in res["error"] and "volatility" in res["error"]


class TestCellSplitMigration:
    """scripts/split_strategies_by_cell.py — rebuilding a declaration from a tag.

    The axis order is not uniform: intraday and swing tags read
    timescale/direction/volatility, position reads
    timescale/direction/volatility/macro but ALSO appears as
    timescale/direction/macro when no volatility was recorded. Getting this
    wrong silently mislabels a migrated strategy's cell, which is worse than
    not migrating it.
    """

    def _mod(self):
        import importlib.util
        from pathlib import Path
        p = (Path(__file__).resolve().parents[2]
             / "scripts" / "split_strategies_by_cell.py")
        spec = importlib.util.spec_from_file_location("split_by_cell", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_slug_drops_the_timescale(self):
        assert self._mod().cell_slug("swing/ranging/compressed") == "ranging-compressed"

    def test_swing_tag_maps_direction_and_volatility(self):
        assert self._mod().cell_declaration("swing", "swing/ranging/compressed") == {
            "swing": {"direction": ["ranging"], "volatility": ["compressed"]}}

    def test_position_tag_with_macro_third(self):
        assert self._mod().cell_declaration(
            "position", "position/ranging/compressed/neutral") == {
            "position": {"direction": ["ranging"], "volatility": ["compressed"],
                         "macro": ["neutral"]}}

    def test_position_tag_where_the_second_axis_is_macro(self):
        """position/trending-down/risk-off has no volatility component."""
        assert self._mod().cell_declaration(
            "position", "position/trending-down/risk-off") == {
            "position": {"direction": ["trending-down"], "macro": ["risk-off"]}}

    def test_declarations_are_single_valued(self):
        m = self._mod()
        for tag, ts in (("swing/ranging/compressed", "swing"),
                        ("position/ranging/compressed/neutral", "position"),
                        ("intraday/trending-up/normal", "intraday")):
            for axis in m.cell_declaration(ts, tag)[ts].values():
                assert len(axis) == 1, tag


class TestCellCapAdmission:
    """The cap is admission control, refused in the writer.

    M is cell-scoped, so every book admitted to a cell raises the graduation
    hurdle for the others in it. The cap existed as prose in reflect's brief
    since the rebuild and was never enforced anywhere — which is how 88
    strategies came to sit across 34 cells with 23 of them over.
    """

    def _tool(self):
        import json as _json

        import trading.dispatchers.strategy_tools  # noqa: F401 — registers
        from harness.tools.registry import registry as tool_registry
        entry = tool_registry.get_entry("strategy_upsert")
        return lambda args: _json.loads(entry.handler(args))

    def _args(self, name, direction="ranging"):
        return {
            "name": name, "timescale": "swing", "mechanism_family": "flow",
            "regime_applicability": {"swing": {"direction": [direction],
                                               "volatility": ["compressed"]}},
            "data_points": [{"name": "dp", "params": {}, "weight": 0.4}],
            "missing_data_points": ["dp"],
            "body": ("# Hypothesis\nx\n\n# Mechanism\ny\n\n"
                     "# Trigger\nz\n\n# Invalidation\nw\n"),
        }

    def _fill(self, conn, call, n):
        from trading.lifecycle.queries import CELL_OCCUPANCY_CAP
        for i in range(min(n, CELL_OCCUPANCY_CAP)):
            assert call(self._args(f"occupant-{i}")).get("ok")

    def test_admits_until_the_cap(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        call = self._tool()
        self._fill(conn, call, queries_cap())
        row = next(r for r in _caps(conn) if r["cell"] == "swing/ranging/compressed")
        assert row["slots_remaining"] == 0

    def test_refuses_once_full(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        call = self._tool()
        self._fill(conn, call, queries_cap())
        res = call(self._args("one-too-many"))
        assert not res.get("ok")
        assert "full" in res["error"] and "cap" in res["error"]

    def test_a_different_cell_is_unaffected(self, conn, monkeypatch):
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        call = self._tool()
        self._fill(conn, call, queries_cap())
        assert call(self._args("elsewhere", direction="trending-up")).get("ok")

    def test_updating_an_existing_book_is_never_blocked(self, conn, monkeypatch):
        """The cap gates admission, not maintenance."""
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda path=None: conn)
        call = self._tool()
        self._fill(conn, call, queries_cap())
        assert call(self._args("occupant-0")).get("ok")


def queries_cap():
    from trading.lifecycle.queries import CELL_OCCUPANCY_CAP
    return CELL_OCCUPANCY_CAP


def _caps(conn):
    from trading.lifecycle.queries import cell_capacity
    return cell_capacity(conn)
