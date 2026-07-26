"""Runtime hygiene — and above all, what it refuses to delete."""

import sqlite3
import time

import pytest

from trading.lifecycle import hygiene

OLD = time.time() - 90 * 86400
NEW = time.time() - 60


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE action_runs (id INTEGER PRIMARY KEY,
                 action_type TEXT, ts REAL, agent TEXT, session_name TEXT,
                 ok INTEGER, notes_md TEXT)""")
    c.commit()
    return c


@pytest.fixture()
def home(tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "ledger").mkdir()
    (tmp_path / "checkpoints").mkdir()

    _stamp(tmp_path / "sessions" / "old.jsonl", OLD)
    _stamp(tmp_path / "sessions" / "recent.jsonl", NEW)

    # The dangerous pair: a journal FILE and a transcript DIRECTORY that differ
    # by one character in a listing.
    _stamp(tmp_path / "ledger" / "2026-1-01.md", OLD)          # journal, sacred
    old_day = tmp_path / "ledger" / "2026-1-01"
    old_day.mkdir()
    _stamp(old_day / "agent-transcript.md", OLD)
    _stamp(old_day, OLD)

    new_day = tmp_path / "ledger" / "2026-7-26"
    new_day.mkdir()
    _stamp(new_day / "agent-transcript.md", NEW)
    _stamp(new_day, NEW)

    # Runtime root files that must never be candidates.
    for name in ("PLUTUS.md", "lifecycle.db", "config.yaml", "CUTOVER-ARMED"):
        _stamp(tmp_path / name, OLD)
    return tmp_path


def _stamp(path, mtime):
    if not path.exists():
        if path.suffix or "." in path.name:
            path.write_text("x")
        else:
            path.write_text("x")
    import os
    os.utime(path, (mtime, mtime))


class TestSafety:
    def test_journals_are_never_deleted(self, conn, home):
        """ledger/2026-1-01.md is the record; ledger/2026-1-01/ is debug
        spoil. They differ by one character and one is precious."""
        hygiene.sweep(conn, home=home, force=True)
        assert (home / "ledger" / "2026-1-01.md").exists()
        assert not (home / "ledger" / "2026-1-01").exists()

    def test_a_live_checkpoint_repo_is_not_gutted(self, conn, home):
        """checkpoints/<id>/ are bare git repos. Git objects keep ancient
        mtimes even in an active repo, so file-level ageing would delete old
        objects while leaving refs pointing at them — corrupting the repo
        instead of removing it. A checkpoint ages by its NEWEST file."""
        repo = home / "checkpoints" / "abc123"
        (repo / "objects" / "f0").mkdir(parents=True)
        _stamp(repo / "objects" / "f0" / "ancient", OLD)
        _stamp(repo / "HEAD", OLD)
        _stamp(repo / "refs", NEW)          # one recent write = live repo
        hygiene.sweep(conn, home=home, force=True)
        assert (repo / "objects" / "f0" / "ancient").exists(), "gutted a live repo"
        assert (repo / "HEAD").exists()

    def test_a_fully_stale_checkpoint_goes_whole(self, conn, home):
        repo = home / "checkpoints" / "dead99"
        (repo / "objects").mkdir(parents=True)
        _stamp(repo / "objects" / "old", OLD)
        _stamp(repo / "HEAD", OLD)
        hygiene.sweep(conn, home=home, force=True)
        assert not repo.exists()

    def test_runtime_root_is_untouched(self, conn, home):
        hygiene.sweep(conn, home=home, force=True)
        for name in ("PLUTUS.md", "lifecycle.db", "config.yaml", "CUTOVER-ARMED"):
            assert (home / name).exists(), f"{name} must never be a candidate"

    def test_recent_files_survive(self, conn, home):
        hygiene.sweep(conn, home=home, force=True)
        assert (home / "sessions" / "recent.jsonl").exists()
        assert (home / "ledger" / "2026-7-26").exists()

    def test_dry_run_removes_nothing(self, conn, home):
        out = hygiene.sweep(conn, home=home, dry_run=True)
        assert out["dry_run"] and out["per_dir"]["sessions"]["removed"] == 1
        assert (home / "sessions" / "old.jsonl").exists()
        assert (home / "ledger" / "2026-1-01").exists()


class TestSweep:
    def test_removes_aged_files(self, conn, home):
        out = hygiene.sweep(conn, home=home, force=True)
        assert out["ok"] and out["removed"] >= 2
        assert not (home / "sessions" / "old.jsonl").exists()

    def test_records_an_action_run(self, conn, home):
        hygiene.sweep(conn, home=home, force=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM action_runs WHERE action_type='hygiene'"
        ).fetchone()[0]
        assert n == 1

    def test_self_gates_between_sweeps(self, conn, home):
        hygiene.sweep(conn, home=home, force=True)
        second = hygiene.sweep(conn, home=home)
        assert second["skipped"] is True
        assert "last sweep" in second["reason"]

    def test_sweeps_again_once_the_interval_passes(self, conn, home):
        conn.execute("INSERT INTO action_runs (action_type, ts, agent, ok) "
                     "VALUES ('hygiene', ?, 'plutus-ops', 1)",
                     (time.time() - hygiene.SWEEP_INTERVAL_S - 60,))
        conn.commit()
        assert hygiene.sweep(conn, home=home)["skipped"] is False

    def test_missing_subdirs_are_not_an_error(self, conn, tmp_path):
        out = hygiene.sweep(conn, home=tmp_path, force=True)
        assert out["ok"] and out["removed"] == 0


class TestTool:
    def test_registered_under_resolution(self):
        from harness.tools import registry as reg
        import trading.dispatchers.hygiene  # noqa: F401
        assert reg.registry.get_toolset_for_tool("runtime_hygiene") == "resolution"
