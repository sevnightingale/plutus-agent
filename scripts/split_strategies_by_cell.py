#!/usr/bin/env python3
"""Split multi-cell strategies into one strategy per regime cell.

Strategies could declare a SET of regimes until 2026-07-27, so a book averaged
trades that share no stop, target or horizon and the average described none of
them. `strategy_upsert` now refuses a set-valued declaration, but books
authored before the rule are still blended — and still GRADUATE on the blend.

This migrates them. It does NOT pick a best cell: choosing the strongest of a
strategy's cells is a selection over trials, the same lucky-winner problem the
multiplicity hurdle exists to catch, and on 5-sample cells it reliably picks
noise. Every cell that has a book becomes its own strategy, and the
multiplicity premium prices the extra trials honestly — which is what it is
for.

Each cell inherits exactly its own predictions, resolved and open, matched on
the `regime_tag` recorded at registration. Nothing is re-simulated and no
outcome is rewritten; only attribution moves, and `parent_strategy` preserves
the lineage.

Cells already judged dead (>= CELL_MIN_N resolutions, expectancy <= 0) are
created DORMANT: they stop consuming prediction budget but keep counting
toward the bar, because dormancy is not evidence of death and only evidence
may lower the hurdle. Thin cells are created as `test` to keep accruing. The
parent is left retired with an empty book.

Usage:
  scripts/split_strategies_by_cell.py --dry-run
  scripts/split_strategies_by_cell.py --apply
"""
from __future__ import annotations

import argparse
import json

import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.lifecycle.db import get_db                      # noqa: E402
from trading.lifecycle.queries import (CELL_MIN_N,           # noqa: E402
                                       strategy_cell_expectancy)
from trading.strategies import loader                        # noqa: E402
from trading.strategies.files import (Strategy, parse_strategy,  # noqa: E402
                                      strategies_dir)

SEP = "--"


def cell_slug(regime_tag: str) -> str:
    """'swing/ranging/compressed' -> 'ranging-compressed'."""
    return "-".join(regime_tag.split("/")[1:])


def cell_declaration(timescale: str, regime_tag: str) -> dict:
    """Rebuild a single-cell regime_applicability from the recorded tag.

    Axis order follows the tag the desk writes: direction, then volatility,
    except at position scale where the third axis is macro.
    """
    parts = regime_tag.split("/")[1:]
    axes = {}
    if parts:
        axes["direction"] = [parts[0]]
    if len(parts) > 1:
        axes["volatility" if timescale != "position" or len(parts) > 2
             else "macro"] = [parts[1]]
    if len(parts) > 2:
        axes["macro"] = [parts[2]]
    return {timescale: axes}


def plan(conn):
    rows = conn.execute(
        """SELECT * FROM strategies WHERE status IN ('test','active')"""
    ).fetchall()
    out = []
    for r in rows:
        tags = [x[0] for x in conn.execute(
            """SELECT regime_tag FROM predictions
               WHERE strategy_name = ? AND regime_tag IS NOT NULL
               GROUP BY regime_tag ORDER BY COUNT(*) DESC""", (r["name"],))]
        if len(tags) < 2:
            continue                      # already effectively single-cell
        verdict = {c["regime_tag"]: c
                   for c in strategy_cell_expectancy(conn, r["name"])["cells"]}
        cells = []
        for tag in tags:
            v = verdict.get(tag) or {}
            n, exp = v.get("n") or 0, v.get("expectancy_pct")
            dead = n >= CELL_MIN_N and exp is not None and exp <= 0
            cells.append({"regime_tag": tag, "n": n, "expectancy_pct": exp,
                          "status": "retired" if dead else "test",
                          "name": f"{r['name']}{SEP}{cell_slug(tag)}"})
        out.append({"parent": r, "cells": cells})
    return out


def apply(conn, items):
    made = 0
    for item in items:
        p = item["parent"]
        # The body lives in the FILE, not the strategies table — the file is
        # truth and the row is its mirror.
        try:
            parent_body = parse_strategy(Path(p["file_path"])).body_md or ""
        except Exception:
            parent_body = ""
        for cell in item["cells"]:
            if conn.execute("SELECT 1 FROM strategies WHERE name=?",
                            (cell["name"],)).fetchone():
                continue
            s = Strategy(
                name=cell["name"], status=cell["status"],
                timescale=p["timescale"], mechanism_family=p["mechanism_family"],
                file_path=strategies_dir() / f"{cell['name']}.md",
                parent_strategy=p["name"],
                variant_tweak=f"regime cell {cell['regime_tag']}",
                regime_applicability=cell_declaration(p["timescale"],
                                                      cell["regime_tag"]),
                data_points=json.loads(p["data_points_json"] or "[]"),
                created=time.strftime("%Y-%m-%d"),
                body_md=parent_body
                + f"\n\n# Cell\nSpecialisation of `{p['name']}` to "
                  f"`{cell['regime_tag']}` (2026-07-27 one-cell migration). "
                  f"Inherits that cell's book only; the parent's other cells "
                  f"became sibling strategies.\n",
            )
            loader.write_strategy(s, conn)
            conn.execute(
                "UPDATE predictions SET strategy_name=? "
                "WHERE strategy_name=? AND regime_tag=?",
                (cell["name"], p["name"], cell["regime_tag"]))
            made += 1
        # File is truth. A raw UPDATE on the mirror left 42 parents
        # `test` on disk and `dormant` in the db (2026-07-27 → 2026-08-12).
        loader.set_status(p["name"], "retired", conn,
                          reason="one-cell split: parent withdrawn; cells inherited the book")
    conn.commit()
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.apply == args.dry_run:
        ap.error("choose exactly one of --dry-run / --apply")

    conn = get_db()
    items = plan(conn)
    cells = [c for i in items for c in i["cells"]]
    print(f"parents to split : {len(items)}")
    print(f"cells to create  : {len(cells)}  "
          f"(test {sum(c['status'] == 'test' for c in cells)}, "
          f"retired/dead {sum(c['status'] == 'retired' for c in cells)})")
    booked = sum(c["n"] for c in cells)
    print(f"resolutions rehomed: {booked}")
    if args.dry_run:
        for i in items[:6]:
            print(f"\n  {i['parent']['name']}")
            for c in i["cells"]:
                e = "-" if c["expectancy_pct"] is None else f"{c['expectancy_pct']:+.3f}"
                print(f"     -> {c['name']:58} n={c['n']:3} exp={e:>8} [{c['status']}]")
        print(f"\n(dry run — nothing written; {len(items) - 6} more parents)")
        return 0

    from trading.lifecycle.db import default_db_path
    db_path = default_db_path()   # respects HERMES_HOME; never assume the home
    backup = db_path.with_suffix(f".db.pre-cellsplit-{int(time.time())}")
    # sqlite3's backup API, NOT shutil.copy: the database runs in WAL mode, so
    # a file copy silently omits everything still in lifecycle.db-wal and
    # restores to a state hours stale. Caught by a rehearsal whose copy showed
    # two strategies still `retired` that had been dormant for an hour.
    import sqlite3
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    print(f"backup: {backup}")
    made = apply(conn, items)
    print(f"created {made} per-cell strategies; parents left retired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
