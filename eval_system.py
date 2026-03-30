#!/usr/bin/env python3
"""Full system evaluation — March 28, 2026"""
import sqlite3, json, os
from datetime import datetime, timezone

c = sqlite3.connect(os.path.expanduser('~/.cama/memory.db'))
c.row_factory = sqlite3.Row

print("=" * 60)
print("CAMA SYSTEM EVALUATION — March 28, 2026")
print("=" * 60)

# 1. Database health
print("\n[1] DATABASE HEALTH")
for r in c.execute('SELECT status, COUNT(*) as cnt FROM memories GROUP BY status ORDER BY cnt DESC'):
    print(f"  {r['status']}: {r['cnt']}")
total = c.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
print(f"  TOTAL: {total}")

# 2. Edges
print("\n[2] EDGE GRAPH")
edge_count = c.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]
print(f"  Total edges: {edge_count}")
for r in c.execute('SELECT edge_type, COUNT(*) as c FROM edges GROUP BY edge_type ORDER BY c DESC'):
    print(f"    {r['edge_type']}: {r['c']}")
has_deg = c.execute("SELECT COUNT(*) as c FROM memories WHERE rel_degree > 0 AND status='durable'").fetchone()["c"]
total_dur = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
print(f"  Memories with rel_degree > 0: {has_deg} / {total_dur} ({round(has_deg/max(1,total_dur)*100,1)}%)")

# 3. Embeddings
print("\n[3] EMBEDDINGS")
has_emb = c.execute("SELECT COUNT(*) as c FROM memories m JOIN memory_embeddings e ON m.id=e.memory_id WHERE m.status='durable'").fetchone()["c"]
no_emb = c.execute("SELECT COUNT(*) as c FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status='durable'").fetchone()["c"]
print(f"  With embeddings: {has_emb}")
print(f"  Without: {no_emb}")

# 4. Affect
print("\n[4] AFFECT")
has_aff = c.execute("SELECT COUNT(*) as c FROM memory_affect").fetchone()["c"]
print(f"  With affect: {has_aff} / {total} ({round(has_aff/max(1,total)*100,1)}%)")

# 5. _parse_t check
print("\n[5] _parse_t FIX")
with open(r'C:\Users\User\Desktop\cama\cama_mcp.py', 'r', encoding='utf-8') as f:
    mcp_content = f.read()
if 'Z-suffix' in mcp_content or "t.endswith('Z')" in mcp_content:
    print("  cama_mcp.py: PATCHED (Z-suffix handling)")
else:
    print("  cama_mcp.py: NOT PATCHED ⚠")

with open(r'C:\Users\User\Desktop\cama\cama_sleep.py', 'r', encoding='utf-8') as f:
    sleep_content = f.read()
if 'Z-suffix' in sleep_content or "t.endswith('Z')" in sleep_content:
    print("  cama_sleep.py: PATCHED")
else:
    print("  cama_sleep.py: NOT PATCHED ⚠")

# 6. Auto-record
print("\n[6] AUTO-RECORD")
if '_exchange_buffer' in mcp_content or '_buf_track' in mcp_content:
    print("  cama_mcp.py: INSTALLED")
else:
    print("  cama_mcp.py: NOT INSTALLED ⚠")

# 7. Sleep daemon version
print("\n[7] SLEEP DAEMON")
if 'v2.1' in sleep_content:
    print("  Version: v2.1 (cross-temporal)")
elif 'v2' in sleep_content:
    print("  Version: v2.0 (rolling window)")
else:
    print("  Version: v1 (original)")

if 'cosine_sim' in sleep_content or 'sem_sim' in sleep_content:
    print("  Embedding gate: INSTALLED")
else:
    print("  Embedding gate: NOT INSTALLED ⚠")

# 8. Sleep daemon scheduled task
print("\n[8] SCHEDULED TASK")
import subprocess
result = subprocess.run(['schtasks', '/query', '/tn', 'CAMA_Sleep_Daemon', '/fo', 'LIST'],
                       capture_output=True, text=True, timeout=10)
if 'CAMA_Sleep_Daemon' in result.stdout:
    for line in result.stdout.split('\n'):
        if 'Status:' in line or 'Last Run Time:' in line or 'Next Run Time:' in line:
            print(f"  {line.strip()}")
else:
    print("  NOT FOUND ⚠")

# 9. Sleep log recent
print("\n[9] RECENT SLEEP CYCLES")
for r in c.execute("SELECT cycle_start, edges_created, memories_expired FROM sleep_log ORDER BY id DESC LIMIT 5"):
    print(f"  {r['cycle_start'][:19]} edges:{r['edges_created']} exp:{r['memories_expired']}")

# 10. Research journal
print("\n[10] RESEARCH JOURNAL")
try:
    rj = c.execute("SELECT COUNT(*) as c FROM research_journal").fetchone()["c"]
    print(f"  Entries: {rj}")
    for r in c.execute("SELECT entry_type, COUNT(*) as c FROM research_journal GROUP BY entry_type ORDER BY c DESC"):
        print(f"    {r['entry_type']}: {r['c']}")
except:
    print("  Table not found ⚠")

# 11. Provisionals
print("\n[11] PROVISIONALS")
prov = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='provisional'").fetchone()["c"]
no_ttl = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='provisional' AND review_after IS NULL").fetchone()["c"]
print(f"  Total: {prov}")
print(f"  Without TTL: {no_ttl}")

# 12. Today's memories
print("\n[12] TODAY'S MEMORIES")
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
today_count = c.execute("SELECT COUNT(*) as c FROM memories WHERE created_at LIKE ?", (today+"%",)).fetchone()["c"]
print(f"  Date: {today}")
print(f"  Count: {today_count}")

# 13. Git status
print("\n[13] GIT STATUS")
result = subprocess.run(['git', '-C', r'C:\Users\User\Desktop\cama', 'status', '--short'],
                       capture_output=True, text=True, timeout=10)
if result.stdout.strip():
    print(f"  Uncommitted changes:")
    for line in result.stdout.strip().split('\n')[:10]:
        print(f"    {line}")
else:
    print("  Clean — all committed")

c.close()
print("\n" + "=" * 60)
print("EVALUATION COMPLETE")
print("=" * 60)
