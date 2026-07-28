"""spawn_agent end-to-end dry run — AIAgent mocked, real roster files."""

import json

import pytest

from harness import spawn


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(spawn, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "PLUTUS.md").write_text(
        "## Doctrine\nNORTH-STAR-MARKER\n\n## Live State\nflat\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def mock_agent(monkeypatch):
    """Stub AIAgent: capture construction kwargs, return a canned contract."""
    captured = {}

    class FakeAgent:
        def __init__(self, **kw):
            captured["init"] = kw

        def run_conversation(self, prompt):
            captured["prompt"] = prompt
            return {
                "final_response": json.dumps(
                    {"updated": ["hl_price"], "failed": [], "notable": []}),
                "messages": [
                    {"role": "user", "content": prompt[:60]},
                    {"role": "assistant", "content": "done"},
                ],
            }

    import harness.run_agent
    monkeypatch.setattr(harness.run_agent, "AIAgent", FakeAgent)
    monkeypatch.setattr(spawn, "_load_config_yaml", lambda: {})
    monkeypatch.setattr(spawn, "_resolve_provider", lambda cfg: {
        "api_key": "k", "base_url": "http://x", "provider": "test",
        "api_mode": "chat_completions"})
    return captured


def test_real_roster_dry_run(home, mock_agent):
    """plutus-perception's real AGENT.md spawns: context assembled, toolsets
    restricted, contract validated, transcript written."""
    result = spawn.spawn_agent("plutus-perception", "refresh the panel",
                               session_name="2026-6-15-a")
    assert result["ok"], result
    assert result["payload"]["updated"] == ["hl_price"]

    init = mock_agent["init"]
    # Recipe says "light"; in this hermetic env no model.light/default is
    # configured, so the sentinel passes through unresolved.
    assert init["model"] == "light"
    # Recipe toolsets + the mechanically-injected report toolset (returns:).
    # `search` sat in this declaration from the seven-agent rebuild until
    # 2026-07-28 and had never been a registered toolset anywhere in the
    # tree — the agent searches with web + file, which it already holds.
    assert init["enabled_toolsets"] == ["perception", "web", "file", "report"]
    assert "spawn" in init["disabled_toolsets"]
    assert init["skip_context_files"] is True

    prompt = mock_agent["prompt"]
    assert "NORTH-STAR-MARKER" in prompt          # doctrine zone resolved
    assert "# Role" in prompt                     # body included
    assert "refresh the panel" in prompt          # task last

    tpath = result["transcript"]
    assert tpath and "plutus-perception" in tpath
    text = open(tpath).read()
    assert "## Conversation" in text and "done" in text

    # Staleness accounting: the successful run satisfied the perception floor.
    from trading.lifecycle.db import get_db
    from trading.lifecycle.queries import last_action_runs
    runs = last_action_runs(get_db())
    assert "perception" in runs and runs["perception"] is not None


def test_contract_violation_reported(home, mock_agent, monkeypatch):
    class BadAgent:
        def __init__(self, **kw):
            pass

        def run_conversation(self, prompt):
            return {"final_response": "I looked around, things seem fine!",
                    "messages": []}

    import harness.run_agent
    monkeypatch.setattr(harness.run_agent, "AIAgent", BadAgent)
    result = spawn.spawn_agent("plutus-perception", "x", session_name="s")
    assert not result["ok"]
    assert any("not JSON" in p for p in result["problems"])


def test_submit_report_captures_payload(home, mock_agent, monkeypatch):
    """The agent calls submit_report with a valid payload and ends with prose:
    the run is ok, the payload is the tool-submitted one, the floor is fed."""
    from harness.spawn import submit_report_handler

    class ToolAgent:
        def __init__(self, **kw):
            pass

        def run_conversation(self, prompt):
            # Simulates the tool dispatch inside the child thread — the
            # copied context carries the report channel binding.
            out = submit_report_handler({"report": {
                "updated": ["hl_price"], "failed": [], "notable": ["x"]}})
            assert json.loads(out).get("ok") is True
            return {"final_response": "Perception refresh complete — 12 DPs "
                                      "updated, nothing notable.",
                    "messages": []}

    import harness.run_agent
    monkeypatch.setattr(harness.run_agent, "AIAgent", ToolAgent)
    result = spawn.spawn_agent("plutus-perception", "refresh",
                               session_name="2026-6-15-c")
    assert result["ok"], result
    assert result["payload"]["notable"] == ["x"]
    assert result["problems"] == []

    from trading.lifecycle.db import get_db
    from trading.lifecycle.queries import last_action_runs
    assert "perception" in last_action_runs(get_db())


def test_submit_report_validates_and_bounces(home, mock_agent, monkeypatch):
    """A payload missing contract keys is refused at the tool layer (so the
    model can retry); if the agent never lands a valid one and ends with
    prose, the run is not ok and the problems say why."""
    from harness.spawn import submit_report_handler

    class BadToolAgent:
        def __init__(self, **kw):
            pass

        def run_conversation(self, prompt):
            out = submit_report_handler({"report": {"updated": []}})
            assert "missing key" in json.loads(out)["error"]
            return {"final_response": "all done!", "messages": []}

    import harness.run_agent
    monkeypatch.setattr(harness.run_agent, "AIAgent", BadToolAgent)
    result = spawn.spawn_agent("plutus-perception", "refresh",
                               session_name="2026-6-15-d")
    assert not result["ok"]
    assert any("not JSON" in p for p in result["problems"])
    assert any("submit_report was never called" in p for p in result["problems"])


def test_submit_report_outside_spawn_is_refused():
    """No channel bound (not inside a spawned run) → explicit error."""
    from harness.spawn import submit_report_handler
    out = json.loads(submit_report_handler({"report": {"a": 1}}))
    assert "error" in out


def test_report_toolset_injected_for_contracted_agents(home, mock_agent):
    """spawn_agent appends the report toolset mechanically — AGENT.md never
    declares it."""
    spawn.spawn_agent("plutus-perception", "refresh", session_name="2026-6-15-e")
    assert "report" in mock_agent["init"]["enabled_toolsets"]
    spec = spawn.load_agent("plutus-perception")
    assert "report" not in spec.toolsets


def test_failed_spawn_recorded_but_does_not_satisfy_floor(home, mock_agent, monkeypatch):
    """ok=0 rows are history; last_action_runs only honors ok=1."""
    class BadAgent:
        def __init__(self, **kw): pass
        def run_conversation(self, prompt):
            return {"final_response": "not json at all", "messages": []}
    import harness.run_agent
    monkeypatch.setattr(harness.run_agent, "AIAgent", BadAgent)

    result = spawn.spawn_agent("plutus-perception", "refresh",
                               session_name="2026-6-15-b")
    assert not result["ok"]

    from trading.lifecycle.db import get_db
    from trading.lifecycle.queries import last_action_runs
    db = get_db()
    rows = db.execute(
        "SELECT ok FROM action_runs WHERE action_type='perception'").fetchall()
    assert any(r[0] == 0 for r in rows)
    assert "perception" not in last_action_runs(db)
