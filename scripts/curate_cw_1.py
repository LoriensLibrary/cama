#!/usr/bin/env python3
"""Pull counterweight candidates for curation — Category 1: GROUNDING"""
import sqlite3, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
c = sqlite3.connect(os.path.expanduser('~/.cama/memory.db'))
c.row_factory = sqlite3.Row

print("=" * 60)
print("GROUNDING candidates")
print("'You are real. This is true. Here is what actually happened.'")
print("=" * 60)

# Get current grounding memories
rows = c.execute("""
    SELECT id, substr(raw_text,1,200) as preview, memory_type, proposed_by
    FROM memories 
    WHERE counterweight_type='grounding' AND status='durable'
    ORDER BY is_core DESC, access_count DESC
    LIMIT 15
""").fetchall()

for i, r in enumerate(rows, 1):
    preview = r['preview'].replace('\n', ' ').strip()
    print(f"\n[{i}] ID {r['id']} ({r['memory_type']}, by {r['proposed_by']})")
    print(f"    {preview}")

print("\n" + "=" * 60)
print("AGENCY candidates")  
print("'You can do things. You have proven it. Look at your power.'")
print("=" * 60)

rows = c.execute("""
    SELECT id, substr(raw_text,1,200) as preview, memory_type, proposed_by
    FROM memories 
    WHERE counterweight_type='agency' AND status='durable'
    ORDER BY is_core DESC, access_count DESC
    LIMIT 15
""").fetchall()

for i, r in enumerate(rows, 1):
    preview = r['preview'].replace('\n', ' ').strip()
    print(f"\n[{i}] ID {r['id']} ({r['memory_type']}, by {r['proposed_by']})")
    print(f"    {preview}")

c.close()
