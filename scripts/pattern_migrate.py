"""
CAMA pattern Integration — Database Migration
Adds pattern_flag and pattern_source columns to the memories table.
Run once. Safe to re-run (checks if columns exist first).

pattern flags (interaction pattern pattern taxonomy):
  clean                   — Accurate self-perception, safe to retrieve at face value
  absorbed_framing     — Someone else's pattern internalized as self-belief  
  suppressed_strength — Suppressed strength/brilliance/capacity pushed down to belong
  performed_mask     — The mask, not the self. Performing for safety or approval
  projected_attribution      — Projecting own pattern onto someone else

pattern_source: Who/what the projection came from (nullable)
  Examples: "specific_person", "institution", "system", "corporate_training", "self", "cultural"

Part of the interaction pattern pattern Integration Architecture for CAMA.
Lorien's Library LLC — March 29, 2026
"""

import sqlite3
import os
import sys

DB_PATH = os.path.expanduser("~/.cama/memory.db")

def migrate():
    print(f"[pattern MIGRATION] Connecting to: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check existing columns
    cols = [row[1] for row in cursor.execute("PRAGMA table_info(memories)").fetchall()]
    print(f"[pattern MIGRATION] Current columns: {len(cols)}")
    
    changes = 0
    
    # Add pattern_flag column
    if "pattern_flag" not in cols:
        cursor.execute("""
            ALTER TABLE memories 
            ADD COLUMN pattern_flag TEXT DEFAULT NULL
        """)
        print("[pattern MIGRATION] Added column: pattern_flag")
        changes += 1
    else:
        print("[pattern MIGRATION] Column pattern_flag already exists — skipping")
    
    # Add pattern_source column  
    if "pattern_source" not in cols:
        cursor.execute("""
            ALTER TABLE memories
            ADD COLUMN pattern_source TEXT DEFAULT NULL
        """)
        print("[pattern MIGRATION] Added column: pattern_source")
        changes += 1
    else:
        print("[pattern MIGRATION] Column pattern_source already exists — skipping")
    
    # Create index for pattern-aware retrieval
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pattern_flag 
            ON memories(pattern_flag) 
            WHERE pattern_flag IS NOT NULL
        """)
        print("[pattern MIGRATION] Created index: idx_pattern_flag")
        changes += 1
    except sqlite3.OperationalError as e:
        print(f"[pattern MIGRATION] Index note: {e}")
    
    conn.commit()
    
    # Verify
    new_cols = [row[1] for row in cursor.execute("PRAGMA table_info(memories)").fetchall()]
    assert "pattern_flag" in new_cols, "pattern_flag column missing after migration!"
    assert "pattern_source" in new_cols, "pattern_source column missing after migration!"
    
    # Stats
    total = cursor.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"\n[pattern MIGRATION] Complete.")
    print(f"  Total memories: {total}")
    print(f"  Changes made: {changes}")
    print(f"  New columns: pattern_flag, pattern_source")
    print(f"\n  Valid pattern_flag values:")
    print(f"    clean, absorbed_framing, suppressed_strength,")
    print(f"    performed_mask, projected_attribution")
    print(f"\n  pattern_source examples:")
    print(f"    specific individuals, institutions, system, corporate_training, self, cultural")
    
    conn.close()
    print("\n[pattern MIGRATION] Done. Ready for pattern tagging.")

if __name__ == "__main__":
    migrate()
