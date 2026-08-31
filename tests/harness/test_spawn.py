"""Spawn mechanism: AGENT.md parsing, reads: resolution, return contracts."""

import json
import pytest

from harness import spawn


def _write_agent(base, name, frontmatter_extra="", body=None):
    d = base / name
    d.mkdir(parents=True)
    body = body if body is not None else "# Role\nTest agent.\n\n# Procedure\n1. Do.\n"
    front = (
        f"---\nname: {name}\nmodel: deepseek-v4-flash\n"
        "toolsets: [perception-read]\nreads:\n  - PLUTUS.md#doctrine\n"
        f"returns: perception_report\nspawned_by: [plutus-main]\n{frontmatter_extra}---\n"
    )
    (d / "AGENT.md").write_text(front + body, encoding="utf-8")
    return d


class TestLoadAgent:
    def test_round_trip(self, tmp_path):
        _write_agent(tmp_path, "plutus-perception")
        spec = spawn.load_agent("plutus-perception", agents_dir=tmp_path)
        assert spec.model == "deepseek-v4-flash"
        assert spec.toolsets == ["perception-read"]
        assert spec.reads == ["PLUTUS.md#doctrine"]
        assert spec.returns == "perception_report"
        assert "# Role" in spec.body_md

    def test_unknown_agent_lists_roster(self, tmp_path):
        _write_agent(tmp_path, "plutus-perception")
        with pytest.raises(FileNotFoundError, match="plutus-perception"):
            spawn.load_agent("plutus-nope", agents_dir=tmp_path)

    def test_name_mismatch_refused(self, tmp_path):
        d = tmp_path / "plutus-x"
        d.mkdir()
        (d / "AGENT.md").write_text(
            "---\nname: other\nmodel: m\n---\nbody", encoding="utf-8")
        with pytest.raises(ValueError, match="!= dir"):
            spawn.load_agent("plutus-x", agents_dir=tmp_path)

    def test_spawn_toolset_is_main_only(self, tmp_path):
        d = tmp_path / "plutus-rogue"
        d.mkdir()
        (d / "AGENT.md").write_text(
            "---\nname: plutus-rogue\nmodel: m\ntoolsets: [spawn]\n---\nbody",
            encoding="utf-8")
        with pytest.raises(ValueError, match="main-only"):
            spawn.load_agent("plutus-rogue", agents_dir=tmp_path)


class TestZones:
    def test_zone_extraction(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "PLUTUS.md").write_text(
            "# PLUTUS\n\n## Doctrine\nbe patient\n\n## Live State\nflat\n\n"
            "## Lessons\nnone yet\n", encoding="utf-8")
        monkeypatch.setattr(spawn, "get_hermes_home", lambda: home)
        doctrine = spawn.resolve_read("PLUTUS.md#doctrine")
        assert "be patient" in doctrine
        assert "flat" not in doctrine
        live = spawn.resolve_read("PLUTUS.md#live-state")
        assert "flat" in live
        whole = spawn.resolve_read("PLUTUS.md")
        assert "be patient" in whole and "flat" in whole

    def test_missing_zone_stated(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "PLUTUS.md").write_text("# PLUTUS\n## Doctrine\nx\n", encoding="utf-8")
        monkeypatch.setattr(spawn, "get_hermes_home", lambda: home)
        out = spawn.resolve_read("PLUTUS.md#lessons")
        assert "no '## lessons' zone" in out

    def test_missing_file_stated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(spawn, "get_hermes_home", lambda: tmp_path)
        assert "does not exist" in spawn.resolve_read("REGIME.md")


class TestAssembleContext:
    def test_failed_read_is_stated_not_dropped(self, tmp_path):
        _write_agent(tmp_path, "plutus-perception")
        spec = spawn.load_agent("plutus-perception", agents_dir=tmp_path)
        spec.reads = ["lifecycle:bogus-block"]
        ctx = spawn.assemble_context(spec, "do the thing")
        assert "READ FAILED" in ctx
        assert "# Task\ndo the thing" in ctx

    def test_order_reads_body_task(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "PLUTUS.md").write_text("## Doctrine\nNORTH-STAR\n", encoding="utf-8")
        monkeypatch.setattr(spawn, "get_hermes_home", lambda: home)
        _write_agent(tmp_path, "plutus-perception")
        spec = spawn.load_agent("plutus-perception", agents_dir=tmp_path)
        ctx = spawn.assemble_context(spec, "TASK-MARKER")
        assert ctx.index("NORTH-STAR") < ctx.index("# Role") < ctx.index("TASK-MARKER")

    def test_reads_are_ordered_slowest_changing_first(self):
        """Volatile blocks must sit below stable ones in the spawn context.

        The provider's prefix cache keeps everything up to the first byte that
        differs from an earlier request, so a block ops rewrites every half
        hour, placed above the doctrine, discards the doctrine on every spawn.
        Measured 2026-08-17: desk agents hit 3-10% on the first call of a spawn
        against 92-97% on later calls in the same run, and the break landed at
        the first volatile block every time.
        """
        ops = ["PLUTUS.md#doctrine", "PLUTUS.md#live-state", "PERCEPTION.md",
               "lifecycle:due-predictions", "lifecycle:open-position"]
        assert spawn._ordered_reads(ops) == [
            "PLUTUS.md#doctrine", "PERCEPTION.md", "PLUTUS.md#live-state",
            "lifecycle:due-predictions", "lifecycle:open-position",
        ]

        predict = ["PLUTUS.md#doctrine", "PLUTUS.md#lessons", "strategies:live",
                   "REGIME.md", "PERCEPTION.md", "lifecycle:open-predictions"]
        got = spawn._ordered_reads(predict)
        # The strategy book turns over faster than the blackboards (generate
        # authors ~30 a day), so it sits below them and above lifecycle.
        assert got.index("strategies:live") > got.index("PERCEPTION.md")
        assert got.index("strategies:live") < got.index("lifecycle:open-predictions")
        assert got[0] == "PLUTUS.md#doctrine"

    def test_unknown_reads_sort_last(self):
        """A read nobody ranked must default to the volatile tail.

        This direction is the whole point: an unranked read placed late costs a
        little cache, where the same read placed early would silently throw
        away every block behind it.
        """
        got = spawn._ordered_reads(
            ["lifecycle:open-position", "some-future-read", "PLUTUS.md#doctrine"])
        assert got[0] == "PLUTUS.md#doctrine"
        assert got[-1] == "some-future-read"

    def test_ordering_is_stable_within_a_rank(self):
        """Blocks that change at the same rate keep the recipe's own order.

        This is a cache optimisation, never a reshuffle of equals — REGIME.md
        and PERCEPTION.md are ranked together and must not swap.
        """
        assert spawn._ordered_reads(["REGIME.md", "PERCEPTION.md"]) == [
            "REGIME.md", "PERCEPTION.md"]
        assert spawn._ordered_reads(["PERCEPTION.md", "REGIME.md"]) == [
            "PERCEPTION.md", "REGIME.md"]

    def test_reads_still_precede_the_body(self, tmp_path, monkeypatch):
        """Reordering reads must not move them below the recipe body.

        plutus-regime's procedure says to read its evidence "from PERCEPTION.md
        (in your context above)", which is only true while every read stays
        above the body.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "PLUTUS.md").write_text("## Doctrine\nNORTH-STAR\n", encoding="utf-8")
        (home / "PERCEPTION.md").write_text("## Readings\nREADINGS-MARKER\n",
                                            encoding="utf-8")
        monkeypatch.setattr(spawn, "get_hermes_home", lambda: home)
        _write_agent(tmp_path, "plutus-perception")
        spec = spawn.load_agent("plutus-perception", agents_dir=tmp_path)
        spec.reads = ["PERCEPTION.md", "PLUTUS.md#doctrine"]
        ctx = spawn.assemble_context(spec, "TASK-MARKER")
        assert ctx.index("NORTH-STAR") < ctx.index("READINGS-MARKER")
        assert ctx.index("READINGS-MARKER") < ctx.index("# Role")
        assert ctx.index("# Role") < ctx.index("TASK-MARKER")

    def test_states_the_runtime_home(self, tmp_path, monkeypatch):
        """Specialists must not have to guess their own data dir — the error
        log carries denied reads against /root/.plutus-agent and
        /home/agent/.plutus-agent from agents hunting for PLUTUS.md."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(spawn, "get_hermes_home", lambda: home)
        _write_agent(tmp_path, "plutus-perception")
        spec = spawn.load_agent("plutus-perception", agents_dir=tmp_path)
        spec.reads = []
        ctx = spawn.assemble_context(spec, "TASK-MARKER")
        assert str(home) in ctx
        assert ctx.index(str(home)) < ctx.index("TASK-MARKER")


class TestReturnContracts:
    def test_valid_payload(self):
        result = spawn.parse_return("perception_report", json.dumps(
            {"updated": ["hl_price"], "failed": [], "notable": []}))
        assert result["ok"] and result["payload"]["updated"] == ["hl_price"]

    def test_fenced_json_tolerated(self):
        text = "Here you go:\n```json\n" + json.dumps(
            {"updated": [], "failed": [], "notable": []}) + "\n```"
        assert spawn.parse_return("perception_report", text)["ok"]

    def test_missing_keys_reported(self):
        result = spawn.parse_return("perception_report", json.dumps({"updated": []}))
        assert not result["ok"]
        assert any("failed" in p for p in result["problems"])

    def test_non_json_reported(self):
        result = spawn.parse_return("regime_report", "I think the regime is fine")
        assert not result["ok"]

    def test_no_contract_passes_text_through(self):
        result = spawn.parse_return(None, "anything")
        assert result["ok"] and result["payload"] is None


def test_desk_models_config_overrides_recipe_model(tmp_path, monkeypatch):
    """config.yaml desk_models wins over AGENT.md frontmatter model."""
    d = tmp_path / "plutus-x"
    d.mkdir()
    (d / "AGENT.md").write_text(
        "---\nname: plutus-x\nmodel: recipe-model\ntoolsets: [perception]\n---\nbody\n"
    )
    import harness.cli.config as cfg
    monkeypatch.setattr(
        cfg, "load_config", lambda: {"desk_models": {"plutus-x": "override-model"}}
    )
    from harness.spawn import load_agent
    assert load_agent("plutus-x", agents_dir=tmp_path).model == "override-model"
    monkeypatch.setattr(cfg, "load_config", lambda: {})
    assert load_agent("plutus-x", agents_dir=tmp_path).model == "recipe-model"


def test_desk_efforts_precedence():
    """desk_efforts[seat] wins over agent.reasoning_effort; '' when neither."""
    from harness.constants import resolve_seat_effort, parse_reasoning_effort
    cfg = {"desk_efforts": {"plutus-predict": "max"},
           "agent": {"reasoning_effort": "low"}}
    assert resolve_seat_effort(cfg, "plutus-predict") == "max"
    assert resolve_seat_effort(cfg, "plutus-ops") == "low"
    assert resolve_seat_effort({}, "plutus-ops") == ""
    # "max" is a valid parse target (DeepSeek's deepest level).
    assert parse_reasoning_effort("max") == {"enabled": True, "effort": "max"}
    assert parse_reasoning_effort("none") == {"enabled": False}


def test_tier_sentinels_resolve_against_user_config(tmp_path, monkeypatch):
    """standard → model.default; light → model.light (else default)."""
    for agent, tier in (("plutus-s", "standard"), ("plutus-l", "light")):
        d = tmp_path / agent
        d.mkdir()
        (d / "AGENT.md").write_text(
            f"---\nname: {agent}\nmodel: {tier}\ntoolsets: [perception]\n---\nbody\n"
        )
    import harness.cli.config as cfg
    from harness.spawn import load_agent
    monkeypatch.setattr(cfg, "load_config", lambda: {
        "model": {"default": "user-model", "light": "user-cheap"}})
    assert load_agent("plutus-s", agents_dir=tmp_path).model == "user-model"
    assert load_agent("plutus-l", agents_dir=tmp_path).model == "user-cheap"
    monkeypatch.setattr(cfg, "load_config", lambda: {"model": {"default": "user-model"}})
    assert load_agent("plutus-l", agents_dir=tmp_path).model == "user-model"


class TestGenerateSplit:
    """2026-07-17: strategy authorship moved predict → plutus-generate."""

    def test_generate_recipe_loads_from_real_roster(self):
        spec = spawn.load_agent("plutus-generate")
        assert spec.returns == "generation_report"
        assert "strategy-write" in spec.toolsets
        assert "strategies:all" in spec.reads      # the global view
        assert spec.model == "standard"

    def test_predict_recipe_is_registration_only(self):
        spec = spawn.load_agent("plutus-predict")
        assert "strategy-write" not in spec.toolsets
        assert "strategy_upsert" not in spec.body_md

    def test_generation_report_contract(self):
        good = {"strategies_authored": [], "registry_survey": {},
                "population_gaps": {}}
        assert spawn.validate_return("generation_report", good) == []
        assert spawn.validate_return("generation_report",
                                     {"strategies_authored": []}) != []

    def test_generate_maps_to_generation_action(self):
        assert spawn._ACTION_TYPES["plutus-generate"] == "generation"


class TestToolsetResolution:
    """A declared toolset that registers no tool must refuse, not degrade.

    The regression: `record_regime`'s dispatcher failed to import, so the
    `regime-write` toolset ceased to exist, and plutus-regime — which declares
    it — spawned anyway with only its `file` tools. It then did the job by hand
    for twelve hours while the database it was meant to write went stale.
    """

    def test_unresolvable_toolset_refuses(self):
        from harness.tools.registry import discover_builtin_tools

        discover_builtin_tools()
        with pytest.raises(ValueError, match="resolve to no registered tool"):
            spawn.require_resolvable_toolsets("plutus-regime", ["regime-write", "no-such-toolset"])

    def test_real_toolset_passes(self):
        from harness.tools.registry import discover_builtin_tools

        discover_builtin_tools()
        spawn.require_resolvable_toolsets("plutus-regime", ["regime-write", "file"])

    def test_silent_when_discovery_has_not_run(self):
        """Discovery is process-global, and another test in this worker may
        already have run it — so the flag is patched, never assumed."""
        from unittest.mock import patch as _patch

        with _patch("harness.tools.registry.builtin_discovery_ran",
                    return_value=False):
            spawn.require_resolvable_toolsets("plutus-regime", ["no-such-toolset"])


class TestContextDiet:
    """The 2026-08-31 spawn-context diet: dark-cell strategy rows and
    claim_md prose were ~140k tokens of freight per predict spawn."""

    def test_strategies_live_is_eligible_only(self, monkeypatch):
        import trading.strategies.loader as loader_mod
        seen = {}

        def _fake(base_dir=None, compact=False, eligible_only=False):
            seen.update(compact=compact, eligible_only=eligible_only)
            return "## Strategy book\n(fake)"

        monkeypatch.setattr(loader_mod, "strategy_context_block", _fake)
        out = spawn.resolve_read("strategies:live")
        assert "(fake)" in out
        assert seen == {"compact": True, "eligible_only": True}

    def test_strategies_all_is_roster_plus_retired(self, monkeypatch):
        import trading.strategies.loader as loader_mod
        monkeypatch.setattr(loader_mod, "roster_context_block",
                            lambda *a, **k: "ROSTER")
        monkeypatch.setattr(loader_mod, "retired_context_block",
                            lambda *a, **k: "GRAVEYARD")
        out = spawn.resolve_read("strategies:all")
        assert "ROSTER" in out and "GRAVEYARD" in out

    def test_open_predictions_claims_trimmed_to_headline(self, monkeypatch):
        import trading.lifecycle.db as db_mod
        import trading.lifecycle.queries as queries_mod
        monkeypatch.setattr(db_mod, "get_db", lambda *a, **k: object())
        long_claim = "HEADLINE up front. " + "drafting prose " * 200
        monkeypatch.setattr(
            queries_mod, "open_predictions",
            lambda conn: [{"id": 1, "strategy_name": "s",
                           "claim_md": long_claim}])
        out = spawn.resolve_read("lifecycle:open-predictions")
        assert "HEADLINE up front." in out
        assert "\\u2026" in out  # the trim marker, json-escaped
        assert len(out) < 1000  # the 3k-char claim did not ride along

    def test_short_claims_pass_untrimmed(self, monkeypatch):
        import trading.lifecycle.db as db_mod
        import trading.lifecycle.queries as queries_mod
        monkeypatch.setattr(db_mod, "get_db", lambda *a, **k: object())
        monkeypatch.setattr(
            queries_mod, "open_predictions",
            lambda conn: [{"id": 1, "claim_md": "short claim"}])
        out = spawn.resolve_read("lifecycle:open-predictions")
        assert "short claim" in out and "\\u2026" not in out
