"""Runtime hygiene — the mechanical half of what plutus-tend used to do.

plutus-tend was built 2026-06-08 because the runtime had accumulated 1.2 GB
and neither standing beat acted on the desk's own house. It ran for three
days, died at the seven-agent rebuild, and its prune script went with it. The
accumulation resumed: ~890 MB at the time of writing, 418 MB of it spawned-
agent transcripts.

Its COGNITIVE half (lessons, weights, dormancy, retirement) was genuinely
absorbed by plutus-reflect and is not restored here. What is restored is the
janitorial part, and it belongs to plutus-ops, whose charter is exactly this
shape: compute and check, never interpret. There is no judgement in deleting
a 40-day-old transcript.

SAFETY. This deletes files, so what it will NOT touch is the important part:

* ``ledger/<date>.md`` — the daily journals, the desk's substantive record
  written by record(kind=eod). Only the sibling ``ledger/<date>/``
  DIRECTORIES, which hold per-spawned-agent debug transcripts, are pruned.
  These look almost identical in a listing and one is precious.
* Anything at the runtime root — the blackboards, lifecycle.db, state.db,
  config.yaml, .env, auth.json, the CUTOVER-ARMED sentinel.
* Any path outside the declared subdirectories below.

The sweep self-gates on `action_runs`: it is safe to call every tick and does
real work about once a day. Gating in CODE rather than in the recipe's prose
is deliberate — the Live State refresh was a prose gate on the cheapest model,
and that pattern is exactly what let an eleven-hour blind spell happen.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# subdirectory -> {days, mode}.
#
# mode="files" prunes individual aged files. mode="dirs" prunes whole
# immediate child directories and never reaches inside them, which matters
# more than it looks:
#
#   * ledger/<date>/ is a pile of transcripts — but its sibling
#     ledger/<date>.md is the journal, so only DIRECTORIES may be candidates.
#   * checkpoints/<id>/ are bare GIT REPOSITORIES. Pruning their files
#     individually would delete old objects while leaving refs pointing at
#     them — corrupting the repo rather than removing it. Worse, git objects
#     keep old mtimes even in a live repo, so file-level ageing would eat an
#     ACTIVE checkpoint. A checkpoint goes whole or not at all, and its age is
#     the newest file inside it, never the directory's own mtime.
RETENTION = {
    "sessions": {"days": 30, "mode": "files"},
    "ledger": {"days": 21, "mode": "dirs", "name_re": r"^\d{4}-\d{1,2}-\d{1,2}$"},
    "checkpoints": {"days": 14, "mode": "dirs"},
    "request_dumps": {"days": 14, "mode": "files"},
    "cron-output": {"days": 14, "mode": "files"},
}

RETENTION_DAYS = {k: v["days"] for k, v in RETENTION.items()}

# Minimum gap between real sweeps. Ops ticks 48x/day; this runs about once.
SWEEP_INTERVAL_S = 20 * 3600


def _last_sweep_ts(conn) -> Optional[float]:
    try:
        row = conn.execute(
            "SELECT MAX(ts) FROM action_runs WHERE action_type='hygiene'"
        ).fetchone()
    except Exception:
        return None
    return row[0] if row and row[0] is not None else None


def _dir_age_and_size(d: Path) -> tuple:
    """(newest mtime inside, total bytes). Newest-inside rather than the
    directory's own mtime: a live git checkpoint has ancient object files and
    a directory stamp that says little."""
    newest, size = 0.0, 0
    for f in d.rglob("*"):
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        newest = max(newest, st.st_mtime)
        size += st.st_size
    if newest == 0.0:
        try:
            newest = d.stat().st_mtime
        except OSError:
            newest = time.time()
    return newest, size


def _candidates(root: Path, spec: Dict[str, Any], cutoff: float):
    """Yield (path, size, is_dir) for everything a sweep would remove."""
    if not root.exists():
        return
    if spec["mode"] == "dirs":
        name_re = re.compile(spec["name_re"]) if spec.get("name_re") else None
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue          # sibling FILES (the journals) are never candidates
            if name_re and not name_re.match(child.name):
                continue
            newest, size = _dir_age_and_size(child)
            if newest < cutoff:
                yield child, size, True
        return
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            yield f, st.st_size, False


def _prune_dir(root: Path, spec: Dict[str, Any], cutoff: float) -> Dict[str, Any]:
    removed, freed, errors = 0, 0, 0
    for path, size, is_dir in list(_candidates(root, spec, cutoff)):
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
            freed += size
        except OSError as exc:
            errors += 1
            logger.warning("hygiene: could not prune %s: %s", path, exc)
    return {"removed": removed, "freed_bytes": freed, "errors": errors}


def runtime_disk_usage(home: Path) -> Dict[str, float]:
    out = {}
    for sub in RETENTION_DAYS:
        d = home / sub
        if not d.exists():
            continue
        try:
            out[sub] = round(
                sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                / (1024 * 1024), 1)
        except OSError:
            continue
    return out


def sweep(conn, home: Optional[Path] = None, force: bool = False,
          dry_run: bool = False) -> Dict[str, Any]:
    """Prune aged runtime files. Self-gating; idempotent; safe every tick."""
    from trading.lifecycle import write

    if home is None:
        from harness.constants import get_hermes_home
        home = get_hermes_home()
    home = Path(home)

    now = time.time()
    last = _last_sweep_ts(conn)
    if not force and not dry_run and last is not None and (now - last) < SWEEP_INTERVAL_S:
        return {"ok": True, "skipped": True,
                "reason": f"last sweep {(now - last) / 3600:.1f}h ago "
                          f"(interval {SWEEP_INTERVAL_S / 3600:.0f}h)"}

    before = runtime_disk_usage(home)
    per_dir, removed, freed, errors = {}, 0, 0, 0
    for sub, spec in RETENTION.items():
        cutoff = now - spec["days"] * 86400
        if dry_run:
            found = list(_candidates(home / sub, spec, cutoff))
            per_dir[sub] = {"removed": len(found),
                            "freed_bytes": sum(s for _, s, _ in found),
                            "errors": 0}
            continue
        res = _prune_dir(home / sub, spec, cutoff)
        per_dir[sub] = res
        removed += res["removed"]
        freed += res["freed_bytes"]
        errors += res["errors"]

    if not dry_run:
        write.record_action_run(
            conn, action_type="hygiene", agent="plutus-ops",
            ok=(errors == 0),
            notes_md=f"pruned {removed} paths, {freed / (1024 * 1024):.1f} MB")

    return {
        "ok": errors == 0,
        "skipped": False,
        "dry_run": dry_run,
        "removed": removed,
        "freed_mb": round(freed / (1024 * 1024), 1),
        "errors": errors,
        "per_dir": per_dir,
        "usage_mb_before": before,
        "usage_mb_after": runtime_disk_usage(home),
        "retention_days": dict(RETENTION_DAYS),
    }
