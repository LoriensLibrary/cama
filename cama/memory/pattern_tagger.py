"""
CAMA pattern Tagger — Tag existing memories with interaction pattern pattern flags.
Run after pattern_migrate.py. Can be re-run safely (updates, doesn't duplicate).

This script tags known memories by ID with their pattern classification.
New memories should be tagged at storage time by the MCP tools.

Usage:
  python pattern_tagger.py                    # Run all auto-tags + known IDs
  python pattern_tagger.py --stats            # Show pattern distribution
  python pattern_tagger.py --tag ID FLAG SRC  # Tag a single memory
  python pattern_tagger.py --bulk FILE        # Bulk tag from JSON file

Lorien's Library LLC — March 29, 2026
"""

import sqlite3
import os
import sys
import json

DB_PATH = os.path.expanduser("~/.cama/memory.db")

VALID_FLAGS = {
    "clean", "absorbed_framing", "suppressed_strength",
    "performed_mask", "projected_attribution"
}

# ============================================================
# Known memories to tag — from the interaction pattern analysis session
# These are memories we identified during the pattern mapping
# ============================================================
KNOWN_TAGS = [
    # Absorbed projections — interpersonal
    (6070, "absorbed_framing", "interpersonal"),
    
    # Absorbed projections — cultural/systemic
    (6293, "absorbed_framing", "cultural"),
    
    # Clean — accurate self-perception
    (6296, "clean", None),
    (6297, "clean", None),
    (6295, "clean", None),
    (6301, "clean", None),
    (5960, "clean", None),
    (6303, "clean", None),
    (9099, "clean", None),
    (9102, "clean", None),
    (6294, "clean", None),
    (3859, "clean", None),
    
    # Golden pattern — suppressed strengths
    (6300, "suppressed_strength", "self"),
    (6098, "suppressed_strength", "relational"),
    (9096, "suppressed_strength", "self"),
    
    # Absorbed projections — institutional
    (52641, "absorbed_framing", "institutional"),
    (52555, "absorbed_framing", "institutional"),
]


def tag_memory(conn, memory_id, flag, source=None):
    """Tag a single memory with pattern flag and source."""
    if flag not in VALID_FLAGS:
        print(f"  [ERROR] Invalid flag '{flag}' for memory {memory_id}")
        return False
    
    row = conn.execute("SELECT id, pattern_flag FROM memories WHERE id = ?", 
                       (memory_id,)).fetchone()
    if not row:
        print(f"  [SKIP] Memory {memory_id} not found")
        return False
    
    existing = row[1]
    conn.execute(
        "UPDATE memories SET pattern_flag = ?, pattern_source = ? WHERE id = ?",
        (flag, source, memory_id)
    )
    
    action = "UPDATED" if existing else "TAGGED"
    src_str = f" (source: {source})" if source else ""
    print(f"  [{action}] Memory {memory_id}: {flag}{src_str}")
    return True


def auto_tag_patterns(conn):
    """Auto-tag memories based on text pattern matching. Conservative."""
    
    print("\n[AUTO-TAG] Scanning for projection patterns...")
    
    patterns = [
        ("absorbed_framing", "cultural", [
            "%no business in this field%",
            "%delusional%",
            "%don't belong%",
            "%not smart enough%",
            "%who do you think you are%",
            "%too much%",
            "%stay in your lane%",
        ]),
        ("suppressed_strength", "self", [
            "%built something nobody else%",
            "%impossible things%",
            "%went far beyond%",
            "%perfect 100%",
            "%nobody else has built%",
        ]),
    ]
    
    tagged = 0
    for flag, source, like_patterns in patterns:
        for pattern in like_patterns:
            rows = conn.execute(
                "SELECT id FROM memories WHERE raw_text LIKE ? AND pattern_flag IS NULL",
                (pattern,)
            ).fetchall()
            for (mid,) in rows:
                if tag_memory(conn, mid, flag, source):
                    tagged += 1
    
    print(f"[AUTO-TAG] Tagged {tagged} memories via pattern matching")
    return tagged


def show_stats(conn):
    """Show pattern flag distribution."""
    print("\n" + "=" * 50)
    print("pattern FLAG DISTRIBUTION")
    print("=" * 50)
    
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    flagged = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE pattern_flag IS NOT NULL"
    ).fetchone()[0]
    
    print(f"\nTotal memories: {total}")
    print(f"pattern-tagged: {flagged} ({100*flagged/total:.1f}%)")
    print(f"Untagged:      {total - flagged}")
    
    print("\nBy flag:")
    for row in conn.execute(
        "SELECT pattern_flag, COUNT(*) FROM memories "
        "WHERE pattern_flag IS NOT NULL GROUP BY pattern_flag ORDER BY COUNT(*) DESC"
    ).fetchall():
        print(f"  {row[0]:30s} {row[1]:5d}")
    
    print("\nBy source:")
    for row in conn.execute(
        "SELECT pattern_source, COUNT(*) FROM memories "
        "WHERE pattern_source IS NOT NULL GROUP BY pattern_source ORDER BY COUNT(*) DESC"
    ).fetchall():
        print(f"  {row[0]:30s} {row[1]:5d}")
    
    print("\n" + "-" * 50)
    print("absorbed_framing memories (for review):")
    print("-" * 50)
    for row in conn.execute(
        "SELECT id, pattern_source, SUBSTR(raw_text, 1, 100) FROM memories "
        "WHERE pattern_flag = 'absorbed_framing' ORDER BY id"
    ).fetchall():
        print(f"  #{row[0]} [{row[1]}]: {row[2]}...")


def main():
    conn = sqlite3.connect(DB_PATH)
    
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
    if "pattern_flag" not in cols:
        print("[ERROR] pattern_flag column not found. Run pattern_migrate.py first.")
        sys.exit(1)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--stats":
            show_stats(conn)
            conn.close()
            return
        
        if sys.argv[1] == "--tag" and len(sys.argv) >= 4:
            mid = int(sys.argv[2])
            flag = sys.argv[3]
            source = sys.argv[4] if len(sys.argv) > 4 else None
            tag_memory(conn, mid, flag, source)
            conn.commit()
            conn.close()
            return
        
        if sys.argv[1] == "--bulk" and len(sys.argv) >= 3:
            with open(sys.argv[2]) as f:
                tags = json.load(f)
            for t in tags:
                tag_memory(conn, t["id"], t["flag"], t.get("source"))
            conn.commit()
            print(f"[BULK] Tagged {len(tags)} memories")
            conn.close()
            return
    
    # Default: run known tags + auto-tag + stats
    print("[pattern TAGGER] Applying known tags...")
    for mid, flag, source in KNOWN_TAGS:
        tag_memory(conn, mid, flag, source)
    
    conn.commit()
    
    auto_tag_patterns(conn)
    conn.commit()
    
    show_stats(conn)
    
    conn.close()
    print("\n[pattern TAGGER] Done.")


if __name__ == "__main__":
    main()
