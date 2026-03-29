#!/usr/bin/env python3
"""Diagnostic: sleep daemon health check"""
import sqlite3, json
from collections import Counter

c = sqlite3.connect(r'C:\Users\Angela\.cama\memory.db')
c.row_factory = sqlite3.Row

# 1. Memory status counts
print('=== MEMORY STATUS COUNTS ===')
for r in c.execute('SELECT status, COUNT(*) as cnt FROM memories GROUP BY status ORDER BY cnt DESC'):
    print(f'  {r["status"]}: {r["cnt"]}')

# 2. Total edges + types
edge_count = c.execute('SELECT COUNT(*) as c FROM edges').fetchone()['c']
print(f'\n=== EDGES: {edge_count} total ===')
for r in c.execute('SELECT edge_type, COUNT(*) as c FROM edges GROUP BY edge_type ORDER BY c DESC'):
    print(f'  {r["edge_type"]}: {r["c"]}')

# 3. rel_degree distribution on durables
print('\n=== REL_DEGREE DISTRIBUTION (durable only) ===')
for r in c.execute('SELECT rel_degree, COUNT(*) as c FROM memories WHERE status="durable" GROUP BY rel_degree ORDER BY rel_degree'):
    print(f'  rel_degree={r["rel_degree"]}: {r["c"]}')

# 4. Provisionals + review_after
prov_total = c.execute('SELECT COUNT(*) as c FROM memories WHERE status="provisional"').fetchone()['c']
prov_with_review = c.execute('SELECT COUNT(*) as c FROM memories WHERE status="provisional" AND review_after IS NOT NULL').fetchone()['c']
print(f'\n=== PROVISIONALS ===')
print(f'  Total provisional: {prov_total}')
print(f'  With review_after set: {prov_with_review}')
print(f'  Without review_after: {prov_total - prov_with_review}')

# 5. Sleep log recent
print('\n=== RECENT SLEEP LOG (last 10) ===')
for r in c.execute('SELECT cycle_start, cycle_end, memories_consolidated, memories_expired, embeddings_backfilled, edges_created, dream_entry FROM sleep_log ORDER BY id DESC LIMIT 10'):
    dream_flag = 'Y' if r['dream_entry'] else 'N'
    print(f'  {r["cycle_start"][:19]} | edges:{r["edges_created"]} expired:{r["memories_expired"]} embeds:{r["embeddings_backfilled"]} dream:{dream_flag}')

# 6. Embeddings coverage
has_emb = c.execute('SELECT COUNT(*) as c FROM memories m JOIN memory_embeddings e ON m.id=e.memory_id WHERE m.status="durable"').fetchone()['c']
no_emb = c.execute('SELECT COUNT(*) as c FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status="durable"').fetchone()['c']
print(f'\n=== EMBEDDINGS ===')
print(f'  Durable with embeddings: {has_emb}')
print(f'  Durable without embeddings: {no_emb}')

# 7. The recurring duplicates - what are they?
print('\n=== RECURRING DUPLICATE CANDIDATES ===')
dupe_ids = [
    (52494, 52506), (52498, 52506), (52503, 52506),
    (6139, 6140), (6208, 6209), (6206, 6209),
    (6200, 6202), (6197, 6202), (6196, 6202), (6194, 6202),
    (4435, 4437), (5003, 5959), (4803, 4805), (9094, 9095)
]
all_ids = set()
for a, b in dupe_ids:
    all_ids.add(a)
    all_ids.add(b)

for mid in sorted(all_ids):
    r = c.execute('SELECT id, raw_text, memory_type, source_type, status, created_at FROM memories WHERE id=?', (mid,)).fetchone()
    if r:
        txt = r['raw_text'][:80].replace('\n', ' ')
        print(f'  #{r["id"]} [{r["memory_type"]}/{r["source_type"]}/{r["status"]}] {r["created_at"][:10]}: {txt}')

# 8. Consolidation window analysis
# How many durable memories exist total vs the 1000 LIMIT
total_durable = c.execute('SELECT COUNT(*) as c FROM memories WHERE status="durable"').fetchone()['c']
print(f'\n=== CONSOLIDATION WINDOW ===')
print(f'  Total durable: {total_durable}')
print(f'  Consolidation window: 1000 (most recent)')
print(f'  Coverage: {min(100, round(1000/max(1,total_durable)*100, 1))}%')

# Date range of the 1000 window
oldest_in_window = c.execute('SELECT created_at FROM memories WHERE status="durable" ORDER BY created_at DESC LIMIT 1 OFFSET 999').fetchone()
if oldest_in_window:
    print(f'  Window oldest: {oldest_in_window["created_at"][:10]}')
newest = c.execute('SELECT created_at FROM memories WHERE status="durable" ORDER BY created_at DESC LIMIT 1').fetchone()
if newest:
    print(f'  Window newest: {newest["created_at"][:10]}')

# 9. How many memories have affect data
has_affect = c.execute('SELECT COUNT(*) as c FROM memory_affect').fetchone()['c']
total_all = c.execute('SELECT COUNT(*) as c FROM memories').fetchone()['c']
print(f'\n=== AFFECT COVERAGE ===')
print(f'  Memories with affect: {has_affect}')
print(f'  Total memories: {total_all}')
print(f'  Coverage: {round(has_affect/max(1,total_all)*100, 1)}%')

# 10. Dream entries
dreams = c.execute("SELECT id, raw_text, created_at FROM memories WHERE memory_type='dream' ORDER BY created_at DESC LIMIT 5").fetchall()
print(f'\n=== RECENT DREAMS ===')
for d in dreams:
    print(f'  #{d["id"]} {d["created_at"][:19]}: {d["raw_text"][:100]}')

c.close()
