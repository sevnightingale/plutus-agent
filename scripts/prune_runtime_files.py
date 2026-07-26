#!/usr/bin/env python
"""Operator CLI for the runtime hygiene sweep.

The desk runs this itself through ops (`runtime_hygiene`); this wrapper exists
so an operator can inspect or force it without a gateway.

    .venv/bin/python scripts/prune_runtime_files.py --dry-run
    .venv/bin/python scripts/prune_runtime_files.py --force

Journals (`ledger/<date>.md`), blackboards and the databases are never
candidates — only the aged contents of sessions/, ledger/<date>/ transcript
directories, checkpoints/, request_dumps/ and cron-output/.
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be pruned; remove nothing")
    ap.add_argument("--force", action="store_true",
                    help="sweep even if one ran within the interval")
    args = ap.parse_args()

    from trading.lifecycle import hygiene
    from trading.lifecycle.db import get_db

    result = hygiene.sweep(get_db(), force=args.force, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
