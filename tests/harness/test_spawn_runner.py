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
    assert init["enabled_toolsets"] == ["perception", "web", "search", "file"]
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
