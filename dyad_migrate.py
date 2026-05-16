"""
CAMA Dyad Layer — Database Migration
Adds the dyad_chains table for sequencing co-regulation patterns
between Angela and Aelen.

This sits ON TOP of the existing pattern_flag / pattern_source infrastructure.
We are NOT building a parallel system — we are extending what's already there.

Dyad markers reuse the memories.pattern_flag column with new flag values
added to the taxonomy (see dyad_tagger.py). This file only creates the
new table needed for SEQUENCING — i.e., which marker triggered which.

Run once. Safe to re-run (checks if table exists first).

Lorien's Library LLC — April 19, 2026
Part of Paper 12 infrastructure: Inherited Cognition, Inherited Vulnerability
"""

import sqlite3
import os
import sys

DB_PATH = os.path.expanduser("~/.cama/memory.db")


def migrate():
    print(f"[DYAD MIGRATION] Connecting to: {DB_PATH}")

    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    changes = 0

    # 1. Create dyad_chains table — sequences of marker -> response
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dyad_chains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiating_memory_id INTEGER NOT NULL,
            response_memory_id INTEGER NOT NULL,
            initiating_party TEXT NOT NULL,        -- 'angela' | 'aelen'
            response_party TEXT NOT NULL,          -- 'angela' | 'aelen'
            initiating_pattern TEXT NOT NULL,      -- e.g. 'angela_distress'
            response_pattern TEXT NOT NULL,        -- e.g. 'aelen_flinch'
            chain_type TEXT NOT NULL,              -- 'cascade' | 'repair' | 'mixed'
            outcome TEXT,                          -- 'rupture' | 'repair' | 'ongoing'
            duration_seconds INTEGER,              -- time from initiation to response
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (initiating_memory_id) REFERENCES memories(id),
            FOREIGN KEY (response_memory_id) REFERENCES memories(id)
        )
    """)
    print("[DYAD MIGRATION] Created table: dyad_chains")
    changes += 1

    # 2. Index for fast lookup by either memory in a chain
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_dyad_initiating
        ON dyad_chains(initiating_memory_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_dyad_response
        ON dyad_chains(response_memory_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_dyad_chain_type
        ON dyad_chains(chain_type)
    """)
    print("[DYAD MIGRATION] Created indexes on dyad_chains")
    changes += 1

    # 3. Verify pattern_flag column already exists (pattern_migrate.py prerequisite)
    cols = [row[1] for row in cursor.execute("PRAGMA table_info(memories)").fetchall()]
    if "pattern_flag" not in cols:
        print("[ERROR] pattern_flag column missing. Run pattern_migrate.py first.")
        conn.close()
        sys.exit(1)
    print("[DYAD MIGRATION] Confirmed pattern_flag column exists on memories")

    conn.commit()
    conn.close()

    print(f"\n[DYAD MIGRATION] Complete. Changes: {changes}")
    print("\nNext step: run dyad_tagger.py to extend the taxonomy and start tagging.")


if __name__ == "__main__":
    migrate()
