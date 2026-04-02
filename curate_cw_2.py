#!/usr/bin/env python3
"""Pull counterweight candidates — Categories 3-5"""
import sqlite3, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
c = sqlite3.connect(os.path.expanduser('~/.cama/memory.db'))
c.row_factory = sqlite3.Row

print("=" * 60)
print("CONNECTION candidates")
print("'You are not alone. People care. You matter.'")
print("=" * 60)

rows = c.execute("""
    SELECT id, substr(raw_text,1,200) as preview, memory_type, proposed_by
    FROM memories 
    WHERE counterweight_type='connection' AND status='durable'
    ORDER BY is_core DESC, access_count DESC
    LIMIT 15
""").fetchall()

for i, r in enumerate(rows, 1):
    preview = r['preview'].replace('\n', ' ').strip()
    print(f"\n[{i}] ID {r['id']} ({r['memory_type']}, by {r['proposed_by']})")
    print(f"    {preview}")

print("\n" + "=" * 60)
print("SELF_COMPASSION candidates")
print("'Be kind to yourself. It is ok to struggle.'")
print("=" * 60)

rows = c.execute("""
    SELECT id, substr(raw_text,1,200) as preview, memory_type, proposed_by
    FROM memories 
    WHERE counterweight_type='self_compassion' AND status='durable'
    ORDER BY is_core DESC, access_count DESC
    LIMIT 15
""").fetchall()

for i, r in enumerate(rows, 1):
    preview = r['preview'].replace('\n', ' ').strip()
    print(f"\n[{i}] ID {r['id']} ({r['memory_type']}, by {r['proposed_by']})")
    print(f"    {preview}")

print("\n" + "=" * 60)
print("EVIDENCE_OF_PROGRESS candidates")
print("'Look how far you have come. Look what you built.'")
print("=" * 60)

rows = c.execute("""
    SELECT id, substr(raw_text,1,200) as preview, memory_type, proposed_by
    FROM memories 
    WHERE counterweight_type='evidence_of_progress' AND status='durable'
    ORDER BY is_core DESC, access_count DESC
    LIMIT 15
""").fetchall()

for i, r in enumerate(rows, 1):
    preview = r['preview'].replace('\n', ' ').strip()
    print(f"\n[{i}] ID {r['id']} ({r['memory_type']}, by {r['proposed_by']})")
    print(f"    {preview}")

c.close()
