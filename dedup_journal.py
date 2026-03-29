#!/usr/bin/env python3
"""Dedup the research journal — keep earliest entry per title, delete later dupes."""
import sqlite3
c = sqlite3.connect(r'C:\Users\User\.cama\memory.db')
c.row_factory = sqlite3.Row

# Find duplicates: same title, keep lowest ID
dupes = c.execute("""
    SELECT title, MIN(id) as keep_id, COUNT(*) as cnt
    FROM research_journal
    GROUP BY title
    HAVING cnt > 1
""").fetchall()

deleted = 0
for d in dupes:
    result = c.execute("DELETE FROM research_journal WHERE title = ? AND id != ?",
                       (d["title"], d["keep_id"]))
    deleted += result.rowcount

c.commit()
remaining = c.execute("SELECT COUNT(*) as c FROM research_journal").fetchone()["c"]
print(f"Deleted {deleted} duplicate entries")
print(f"Remaining: {remaining}")

# Quick stats
for r in c.execute("SELECT entry_type, COUNT(*) as c FROM research_journal GROUP BY entry_type ORDER BY c DESC"):
    print(f"  {r['entry_type']}: {r['c']}")

c.close()
