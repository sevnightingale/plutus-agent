#!/usr/bin/env python3
"""Smoke test for Phase 4a dependencies — sqlite-vec extension + voyage-finance-2.

Verifies:
  1. sqlite-vec extension loads into a fresh sqlite3 connection
  2. voyage-finance-2 returns a 1024-dim embedding from the API
  3. The embedding round-trips through a vec0 virtual table (insert + cosine query)

Requires:
  - VOYAGE_API_KEY in env (or ~/.plutus-agent/.env via dotenv)
  - voyageai + sqlite-vec installed in the active venv

Exit code 0 on success.
"""

import os
import sqlite3
import sys
from pathlib import Path

import sqlite_vec
import voyageai
from dotenv import load_dotenv


HERMES_HOME = Path.home() / ".plutus-agent"
load_dotenv(HERMES_HOME / ".env")


def main() -> int:
    api_key = os.environ.get("VOYAGE_API_KEY", "").strip()
    if not api_key:
        print("FAIL: VOYAGE_API_KEY not set in env or ~/.plutus-agent/.env", file=sys.stderr)
        return 1

    print(f"sqlite_vec loadable: {sqlite_vec.loadable_path()}")

    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    (sqlite_version,) = conn.execute("SELECT sqlite_version()").fetchone()
    (vec_version,) = conn.execute("SELECT vec_version()").fetchone()
    print(f"sqlite_version={sqlite_version}  vec_version={vec_version}")

    conn.execute(
        "CREATE VIRTUAL TABLE smoke_vec USING vec0("
        "  thesis_id INTEGER PRIMARY KEY,"
        "  embedding FLOAT[1024]"
        ")"
    )
    print("vec0 table created (FLOAT[1024])")

    client = voyageai.Client(api_key=api_key)
    text = "BTC funding rate flipped negative; coiled price action below 70k resistance."
    result = client.embed([text], model="voyage-finance-2", input_type="document")
    vector = result.embeddings[0]

    if len(vector) != 1024:
        print(f"FAIL: expected 1024-dim vector, got {len(vector)}", file=sys.stderr)
        return 2

    print(f"voyage-finance-2 returned {len(vector)} dims; first 4 = {vector[:4]}")

    conn.execute(
        "INSERT INTO smoke_vec(thesis_id, embedding) VALUES (?, ?)",
        (1, sqlite_vec.serialize_float32(vector)),
    )
    print("vector inserted into vec0 table")

    rows = conn.execute(
        "SELECT thesis_id, distance FROM smoke_vec "
        "WHERE embedding MATCH ? AND k = 1 ORDER BY distance",
        (sqlite_vec.serialize_float32(vector),),
    ).fetchall()
    if not rows or rows[0][0] != 1:
        print(f"FAIL: nearest-neighbor query did not return inserted row: {rows}", file=sys.stderr)
        return 3

    thesis_id, distance = rows[0]
    print(f"nearest-neighbor query OK: thesis_id={thesis_id}  distance={distance:.6f}")

    conn.close()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
