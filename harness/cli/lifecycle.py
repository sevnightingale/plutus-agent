"""``plutus-agent lifecycle`` CLI subcommands.

Inspect the Plutus lifecycle store at ``~/.plutus-agent/lifecycle.db``:

    plutus-agent lifecycle status
    plutus-agent lifecycle dump <table> [--limit N] [--format json|table]
    plutus-agent lifecycle migrate

Operates on the real lifecycle.db at ``get_hermes_home() / 'lifecycle.db'``,
not a temp DB. ``migrate`` is idempotent — re-runs schema bootstrap (CREATE
IF NOT EXISTS everywhere) so it's safe to call repeatedly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from trading.lifecycle.db import SCHEMA_VERSION, LifecycleDB
from harness.constants import get_hermes_home


# Tables in the canonical print order — base tables first, then virtual tables.
_BASE_TABLES = [
    "schema_version",
    "data_point_snapshots",
    "strategies",
    "theses",
    "decisions",
    "trades",
    "positions",
    "position_evaluations",
    "outcomes",
    "reflections",
    "capital_movements",
]
_FTS_TABLES = ["theses_fts", "reflections_fts"]
_VEC_TABLES = ["theses_vec", "reflections_vec"]
_DUMPABLE_TABLES = set(_BASE_TABLES) | set(_FTS_TABLES) | set(_VEC_TABLES)


def _db_path() -> Path:
    return get_hermes_home() / "lifecycle.db"


def _row_count(conn, table: str) -> Optional[int]:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return row["c"] if row else 0
    except Exception:
        return None


def _print_status() -> int:
    path = _db_path()
    db = LifecycleDB(db_path=path)
    try:
        version_row = db.conn().execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        version = version_row["version"] if version_row else None

        print(f"lifecycle.db: {path}")
        print(f"schema_version (file): {version}   (code: {SCHEMA_VERSION})")
        if version is not None and version != SCHEMA_VERSION:
            print("  ! schema versions differ — run `plutus-agent lifecycle migrate`")
        print()

        print(f"{'table':<30} {'rows':>10}")
        print(f"{'-' * 30} {'-' * 10}")
        for table in _BASE_TABLES + _FTS_TABLES + _VEC_TABLES:
            n = _row_count(db.conn(), table)
            display = f"{n:>10}" if n is not None else f"{'n/a':>10}"
            print(f"{table:<30} {display}")
    finally:
        db.close()
    return 0


def _print_dump(table: str, limit: int, fmt: str) -> int:
    if table not in _DUMPABLE_TABLES:
        print(f"unknown table '{table}'. known: {sorted(_DUMPABLE_TABLES)}",
              file=sys.stderr)
        return 2

    db = LifecycleDB(db_path=_db_path())
    try:
        try:
            rows = db.conn().execute(
                f"SELECT * FROM {table} LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        except Exception as exc:
            print(f"failed to dump {table}: {exc}", file=sys.stderr)
            return 3

        dicts = [dict(r) for r in rows]
        if fmt == "json":
            json.dump(dicts, sys.stdout, default=str, indent=2)
            sys.stdout.write("\n")
            return 0

        # default: human-readable table
        if not dicts:
            print(f"(no rows in {table})")
            return 0
        cols = list(dicts[0].keys())
        widths = {c: max(len(str(c)), max(len(str(d.get(c) or "")) for d in dicts))
                  for c in cols}
        widths = {c: min(w, 40) for c, w in widths.items()}  # cap at 40
        header = "  ".join(c.ljust(widths[c]) for c in cols)
        print(header)
        print("  ".join("-" * widths[c] for c in cols))
        for d in dicts:
            print("  ".join(str(d.get(c) or "")[: widths[c]].ljust(widths[c]) for c in cols))
    finally:
        db.close()
    return 0


def _migrate() -> int:
    """Re-run schema bootstrap; idempotent."""
    path = _db_path()
    db = LifecycleDB(db_path=path)
    try:
        version = db.conn().execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()["version"]
        print(f"lifecycle.db at {path}: schema_version={version} (code={SCHEMA_VERSION})")
        if version == SCHEMA_VERSION:
            print("schema is current; nothing to do.")
        else:
            # Future migrations land in lifecycle_db._init_schema; calling it
            # again here would re-execute them. Today there are none.
            print("schema_version mismatch — re-bootstrap was a no-op for this version.")
    finally:
        db.close()
    return 0


def add_lifecycle_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the `lifecycle` subcommand tree into plutus-agent's CLI."""
    lifecycle_parser = subparsers.add_parser(
        "lifecycle",
        help="Inspect / migrate the Plutus lifecycle store",
        description=(
            "Inspect the SQLite lifecycle store at ~/.plutus-agent/lifecycle.db: "
            "row counts (status), pretty-print table contents (dump), and "
            "re-bootstrap schema (migrate)."
        ),
    )
    lifecycle_subparsers = lifecycle_parser.add_subparsers(dest="lifecycle_command")

    lifecycle_subparsers.add_parser(
        "status", help="Show row counts per table + schema_version"
    )

    dump = lifecycle_subparsers.add_parser(
        "dump", help="Print rows from one table"
    )
    dump.add_argument("table", help=f"Table name. One of: {sorted(_DUMPABLE_TABLES)}")
    dump.add_argument("--limit", type=int, default=20, help="Max rows (default 20)")
    dump.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="Output format (default: table)",
    )

    lifecycle_subparsers.add_parser(
        "migrate", help="Re-run schema bootstrap (idempotent)"
    )

    lifecycle_parser.set_defaults(func=cmd_lifecycle)


def cmd_lifecycle(args: argparse.Namespace) -> int:
    sub = getattr(args, "lifecycle_command", None)
    if sub == "status" or sub is None:
        return _print_status()
    if sub == "dump":
        return _print_dump(args.table, args.limit, args.format)
    if sub == "migrate":
        return _migrate()
    print(f"unknown lifecycle subcommand: {sub}", file=sys.stderr)
    return 2
