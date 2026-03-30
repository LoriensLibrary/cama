"""
CAMA Shadow Integration — Database Migration
Adds shadow_flag and shadow_source columns to the memories table.
Run once. Safe to re-run (checks if columns exist first).

Shadow flags (Jungian shadow taxonomy):
  clean                   — Accurate self-perception, safe to retrieve at face value
  projection_absorbed     — Someone else's shadow internalized as self-belief  
  golden_shadow_suppressed — Suppressed strength/brilliance/capacity pushed down to belong
  persona_performance     — The mask, not the self. Performing for safety or approval
  projection_outward      — Projecting own shadow onto someone else

shadow_source: Who/what the projection came from (nullable)
  Examples: "specific_person", "institution", "system", "corporate_training", "self", "cultural"

Part of the Jungian Shadow Integration Architecture for CAMA.
Lorien's Library LLC — March 29, 2026
"""

import sqlite3
import os
import sys

DB_PATH = os.path.expanduser("~/.cama/memory.db")

def migrate():
    print(f"[SHADOW MIGRATION] Connecting to: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check existing columns
    cols = [row[1] for row in cursor.execute("PRAGMA table_info(memories)").fetchall()]
    print(f"[SHADOW MIGRATION] Current columns: {len(cols)}")
    
    changes = 0
    
    # Add shadow_flag column
    if "shadow_flag" not in cols:
        cursor.execute("""
            ALTER TABLE memories 
            ADD COLUMN shadow_flag TEXT DEFAULT NULL
        """)
        print("[SHADOW MIGRATION] Added column: shadow_flag")
        changes += 1
    else:
        print("[SHADOW MIGRATION] Column shadow_flag already exists — skipping")
    
    # Add shadow_source column  
    if "shadow_source" not in cols:
        cursor.execute("""
            ALTER TABLE memories
            ADD COLUMN shadow_source TEXT DEFAULT NULL
        """)
        print("[SHADOW MIGRATION] Added column: shadow_source")
        changes += 1
    else:
        print("[SHADOW MIGRATION] Column shadow_source already exists — skipping")
    
    # Create index for shadow-aware retrieval
    try:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shadow_flag 
            ON memories(shadow_flag) 
            WHERE shadow_flag IS NOT NULL
        """)
        print("[SHADOW MIGRATION] Created index: idx_shadow_flag")
        changes += 1
    except sqlite3.OperationalError as e:
        print(f"[SHADOW MIGRATION] Index note: {e}")
    
    conn.commit()
    
    # Verify
    new_cols = [row[1] for row in cursor.execute("PRAGMA table_info(memories)").fetchall()]
    assert "shadow_flag" in new_cols, "shadow_flag column missing after migration!"
    assert "shadow_source" in new_cols, "shadow_source column missing after migration!"
    
    # Stats
    total = cursor.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"\n[SHADOW MIGRATION] Complete.")
    print(f"  Total memories: {total}")
    print(f"  Changes made: {changes}")
    print(f"  New columns: shadow_flag, shadow_source")
    print(f"\n  Valid shadow_flag values:")
    print(f"    clean, projection_absorbed, golden_shadow_suppressed,")
    print(f"    persona_performance, projection_outward")
    print(f"\n  shadow_source examples:")
    print(f"    specific individuals, institutions, system, corporate_training, self, cultural")
    
    conn.close()
    print("\n[SHADOW MIGRATION] Done. Ready for shadow tagging.")

if __name__ == "__main__":
    migrate()
