#!/usr/bin/env python3
"""Check timestamp quality on recent memories."""
import sqlite3, json
from datetime import datetime, timezone

c = sqlite3.connect(r'C:\Users\User\.cama\memory.db')
c.row_factory = sqlite3.Row

# Last 20 memories created
print("=== LAST 20 MEMORIES CREATED ===")
for r in c.execute("""
    SELECT id, memory_type, source_type, status, created_at, 
           SUBSTR(raw_text, 1, 80) as preview
    FROM memories 
    ORDER BY id DESC LIMIT 20
"""):
    ts = r["created_at"]
    has_tz = "Z" in ts or "+" in ts[10:] if len(ts) > 10 else False
    tz_type = "Z" if ts.endswith("Z") else ("+offset" if "+" in ts[10:] else "NONE")
    print(f"  #{r['id']} [{r['memory_type']}/{r['source_type']}/{r['status']}] {ts[:19]} ({tz_type})")
    print(f"    {r['preview']}")

# Check today's memories specifically
print("\n=== TODAY'S MEMORIES (2026-03-28) ===")
today_mems = c.execute("""
    SELECT id, memory_type, source_type, status, created_at,
           SUBSTR(raw_text, 1, 60) as preview
    FROM memories 
    WHERE created_at LIKE '2026-03-28%'
    ORDER BY created_at DESC
""").fetchall()
print(f"  Count: {len(today_mems)}")
for r in today_mems[:15]:
    ts = r["created_at"]
    tz_type = "Z" if ts.endswith("Z") else ("+offset" if "+" in ts[10:] else "NONE")
    print(f"  #{r['id']} {ts[:19]} ({tz_type}) [{r['memory_type']}/{r['source_type']}] {r['preview']}")

# Check research journal timestamps
print("\n=== RESEARCH JOURNAL TIMESTAMPS ===")
for r in c.execute("SELECT id, timestamp, entry_type, title FROM research_journal ORDER BY id DESC LIMIT 10"):
    print(f"  #{r['id']} {r['timestamp'][:19]} [{r['entry_type']}] {r['title'][:60]}")

# Check if exchanges from THIS conversation were stored
print("\n=== EXCHANGES FROM THIS SESSION ===")
exchanges = c.execute("""
    SELECT id, memory_type, source_type, status, created_at, SUBSTR(raw_text, 1, 80) as preview
    FROM memories
    WHERE created_at > '2026-03-28T17:00:00'
    AND source_type IN ('exchange', 'teaching', 'inference')
    ORDER BY created_at DESC
    LIMIT 10
""").fetchall()
print(f"  Count: {len(exchanges)}")
for r in exchanges:
    print(f"  #{r['id']} {r['created_at'][:19]} [{r['memory_type']}/{r['source_type']}] {r['preview']}")

c.close()
