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
