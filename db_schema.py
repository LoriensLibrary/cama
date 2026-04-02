#!/usr/bin/env python3
"""Quick DB schema dump"""
import sqlite3, os
c = sqlite3.connect(os.path.expanduser('~/.cama/memory.db'))

print("=== TABLES ===")
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for t in sorted(tables):
    cols = [(col[1], col[2]) for col in c.execute(f'PRAGMA table_info({t})').fetchall()]
    cnt = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f"\n{t} ({cnt} rows):")
    for name, typ in cols:
        print(f"  {name} ({typ})")

# Quick provenance check
print("\n=== PROVENANCE DATA ===")
print("memory_type distribution:")
for r in c.execute("SELECT memory_type, COUNT(*) as c FROM memories GROUP BY memory_type ORDER BY c DESC"):
    print(f"  {r[0]}: {r[1]}")

print("\nsource_type distribution:")
for r in c.execute("SELECT source_type, COUNT(*) as c FROM memories GROUP BY source_type ORDER BY c DESC"):
    print(f"  {r[0]}: {r[1]}")

print("\nproposed_by distribution:")
for r in c.execute("SELECT proposed_by, COUNT(*) as c FROM memories GROUP BY proposed_by ORDER BY c DESC"):
    print(f"  {r[0]}: {r[1]}")

print("\nstatus distribution:")
for r in c.execute("SELECT status, COUNT(*) as c FROM memories GROUP BY status ORDER BY c DESC"):
    print(f"  {r[0]}: {r[1]}")

# Check if any memories have mismatched provenance
print("\n=== PROVENANCE INTEGRITY ===")
# Teachings proposed by system (should be rare/zero)
sys_teach = c.execute("SELECT COUNT(*) FROM memories WHERE memory_type='teaching' AND proposed_by='system'").fetchone()[0]
print(f"Teachings proposed by system: {sys_teach}")

# Inferences proposed by user (should be rare/zero)
user_inf = c.execute("SELECT COUNT(*) FROM memories WHERE memory_type='inference' AND proposed_by='user'").fetchone()[0]
print(f"Inferences proposed by user: {user_inf}")

# Exchanges
exch = c.execute("SELECT COUNT(*) FROM memories WHERE memory_type='exchange'").fetchone()[0]
print(f"Exchanges: {exch}")

# Check counterweight_type values
print("\n=== COUNTERWEIGHTS ===")
for r in c.execute("SELECT counterweight_type, COUNT(*) as c FROM memories WHERE counterweight_type IS NOT NULL GROUP BY counterweight_type ORDER BY c DESC"):
    print(f"  {r[0]}: {r[1]}")
null_cw = c.execute("SELECT COUNT(*) FROM memories WHERE counterweight_type IS NULL").fetchone()[0]
print(f"  NULL (no counterweight): {null_cw}")

# Shadow flags
print("\n=== SHADOW FLAGS ===")
for r in c.execute("SELECT shadow_flag, COUNT(*) as c FROM memories WHERE shadow_flag IS NOT NULL GROUP BY shadow_flag ORDER BY c DESC"):
    print(f"  {r[0]}: {r[1]}")

c.close()
