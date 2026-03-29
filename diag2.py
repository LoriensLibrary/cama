#!/usr/bin/env python3
"""Diagnostic part 2: duplicates, rel_degree, window, sleep log, dreams"""
import sqlite3, json
c = sqlite3.connect(r'C:\Users\User\.cama\memory.db')
c.row_factory = sqlite3.Row

# 1. rel_degree distribution
print('=== REL_DEGREE DISTRIBUTION (durable) ===')
for r in c.execute('SELECT rel_degree, COUNT(*) as c FROM memories WHERE status="durable" GROUP BY rel_degree ORDER BY rel_degree'):
    print(f'  rel_degree={r["rel_degree"]}: {r["c"]}')

# 2. Consolidation window
print('\n=== CONSOLIDATION WINDOW ===')
oldest = c.execute('SELECT created_at FROM memories WHERE status="durable" ORDER BY created_at DESC LIMIT 1 OFFSET 999').fetchone()
newest = c.execute('SELECT created_at FROM memories WHERE status="durable" ORDER BY created_at DESC LIMIT 1').fetchone()
if oldest: print(f'  Window oldest: {oldest["created_at"][:19]}')
if newest: print(f'  Window newest: {newest["created_at"][:19]}')
print(f'  Total durable: 47294, window: 1000, coverage: {round(1000/47294*100,1)}%')

# 3. Recent sleep log
print('\n=== SLEEP LOG (last 10) ===')
for r in c.execute('SELECT cycle_start, edges_created, memories_expired, embeddings_backfilled, dream_entry FROM sleep_log ORDER BY id DESC LIMIT 10'):
    d = 'Y' if r['dream_entry'] else 'N'
    print(f'  {r["cycle_start"][:19]} edges:{r["edges_created"]} exp:{r["memories_expired"]} emb:{r["embeddings_backfilled"]} dream:{d}')

# 4. Total sleep cycles
total_cycles = c.execute('SELECT COUNT(*) as c FROM sleep_log').fetchone()['c']
total_edges_from_sleep = c.execute('SELECT SUM(edges_created) as s FROM sleep_log').fetchone()['s']
total_expired_from_sleep = c.execute('SELECT SUM(memories_expired) as s FROM sleep_log').fetchone()['s']
print(f'\n=== SLEEP TOTALS ===')
print(f'  Total cycles run: {total_cycles}')
print(f'  Total edges created by sleep: {total_edges_from_sleep}')
print(f'  Total expired by sleep: {total_expired_from_sleep}')

# 5. Dreams
print('\n=== RECENT DREAMS ===')
for d in c.execute("SELECT id, raw_text, created_at FROM memories WHERE memory_type='dream' ORDER BY created_at DESC LIMIT 5"):
    print(f'  #{d["id"]} {d["created_at"][:19]}: {d["raw_text"][:120]}')

# 6. The flagged duplicates - sample a few pairs
print('\n=== DUPLICATE PAIRS (sample) ===')
pairs = [(52494,52506),(6139,6140),(4803,4805),(9094,9095)]
for a, b in pairs:
    ra = c.execute('SELECT id, raw_text, memory_type, source_type FROM memories WHERE id=?', (a,)).fetchone()
    rb = c.execute('SELECT id, raw_text, memory_type, source_type FROM memories WHERE id=?', (b,)).fetchone()
    if ra and rb:
        print(f'  --- Pair #{a} / #{b} ---')
        print(f'    #{a} [{ra["memory_type"]}/{ra["source_type"]}]: {ra["raw_text"][:80]}')
        print(f'    #{b} [{rb["memory_type"]}/{rb["source_type"]}]: {rb["raw_text"][:80]}')

c.close()
