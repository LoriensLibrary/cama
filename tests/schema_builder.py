"""Build the production memory schema for tests.

Not a test module. pytest only collects test_*.py, and nothing here runs
at import time. The name deliberately avoids a leading underscore: the
repo gitignores _*.py for local scratch scripts, so tests/_schema.py
would have been absent from the repo and CI would have failed on the
import.

Both API test modules used to carry their own hand-written subset of the
memory schema. Those subsets drifted: they made ``updated_at`` nullable
while ``cama_mcp._init`` declares it NOT NULL, and they declared no
foreign keys at all, so an API insert that fails against a real database
and a delete that leaves orphaned edges both passed in CI. Tests that
pin architectural guarantees have to run against the DDL a live CAMA
actually has, or they pin nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def init_production_memory_schema(db_path: str | Path) -> None:
    """Create the real memory schema at ``db_path``.

    ``cama_mcp._init`` for the memory tables, the librarian module's own
    DDL for the librarian tables, plus the ``dyad_id`` column the API
    migration adds to existing databases.
    """
    import cama_mcp
    from cama.librarian import cama_librarian

    saved = cama_mcp.DB_PATH
    cama_mcp.DB_PATH = str(db_path)
    try:
        conn = cama_mcp.get_db()
        conn.close()
    finally:
        cama_mcp.DB_PATH = saved

    c = sqlite3.connect(str(db_path))
    try:
        c.executescript(cama_librarian.SCHEMA_SQL)
        cols = {r[1] for r in c.execute("PRAGMA table_info(memories)").fetchall()}
        if "dyad_id" not in cols:
            c.execute(
                "ALTER TABLE memories ADD COLUMN dyad_id TEXT NOT NULL "
                "DEFAULT 'default'"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_dyad ON memories(dyad_id)"
            )
        c.commit()
    finally:
        c.close()
