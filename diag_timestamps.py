#!/usr/bin/env python3
"""Diagnostic: timestamp format distribution"""
import sqlite3
from datetime import datetime, timezone

c = sqlite3.connect(r'C:\Users\Angela\.cama\memory.db')
c.row_factory = sqlite3.Row

rows = c.execute("SELECT created_at FROM memories WHERE status='durable' LIMIT 5000").fetchall()

z_suffix = 0
plus_suffix = 0
no_tz = 0
other = 0
parse_fail = 0

for r in rows:
    t = r["created_at"]
    if t.endswith("Z"):
        z_suffix += 1
    elif "+" in t[10:]:
        plus_suffix += 1
    elif "T" in t and len(t) > 19:
        no_tz += 1
    else:
        other += 1
    
    try:
        datetime.fromisoformat(t)
    except:
        parse_fail += 1

total = len(rows)
print(f"Sampled: {total}")
print(f"  Ends with Z: {z_suffix} ({round(z_suffix/total*100,1)}%)")
print(f"  Has +offset: {plus_suffix} ({round(plus_suffix/total*100,1)}%)")
print(f"  No timezone: {no_tz} ({round(no_tz/total*100,1)}%)")
print(f"  Other: {other} ({round(other/total*100,1)}%)")
print(f"  fromisoformat fails: {parse_fail} ({round(parse_fail/total*100,1)}%)")

# Show examples
print("\nExamples:")
for r in c.execute("SELECT created_at FROM memories WHERE status='durable' AND created_at LIKE '%Z' LIMIT 3"):
    print(f"  Z-suffix: {r['created_at']}")
for r in c.execute("SELECT created_at FROM memories WHERE status='durable' AND created_at LIKE '%+%' LIMIT 3"):
    print(f"  +offset:  {r['created_at']}")
for r in c.execute("SELECT created_at FROM memories WHERE status='durable' AND created_at NOT LIKE '%Z' AND created_at NOT LIKE '%+%' LIMIT 3"):
    print(f"  No TZ:    {r['created_at']}")

c.close()
