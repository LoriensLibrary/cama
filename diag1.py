#!/usr/bin/env python3
"""Diagnostic part 1: counts only"""
import sqlite3
c = sqlite3.connect(r'C:\Users\User\.cama\memory.db')
c.row_factory = sqlite3.Row

print('=== MEMORY STATUS ===')
for r in c.execute('SELECT status, COUNT(*) as cnt FROM memories GROUP BY status ORDER BY cnt DESC'):
    print(f'  {r["status"]}: {r["cnt"]}')

print('\n=== EDGES ===')
print(f'  Total: {c.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]}')
for r in c.execute('SELECT edge_type, COUNT(*) as c FROM edges GROUP BY edge_type ORDER BY c DESC'):
    print(f'  {r["edge_type"]}: {r["c"]}')

print('\n=== PROVISIONALS ===')
pt = c.execute('SELECT COUNT(*) as c FROM memories WHERE status="provisional"').fetchone()['c']
pr = c.execute('SELECT COUNT(*) as c FROM memories WHERE status="provisional" AND review_after IS NOT NULL').fetchone()['c']
print(f'  Total: {pt}, With review_after: {pr}, Without: {pt-pr}')

print('\n=== EMBEDDINGS ===')
he = c.execute('SELECT COUNT(*) as c FROM memories m JOIN memory_embeddings e ON m.id=e.memory_id WHERE m.status="durable"').fetchone()['c']
ne = c.execute('SELECT COUNT(*) as c FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status="durable"').fetchone()['c']
print(f'  Durable with: {he}, without: {ne}')

print('\n=== AFFECT ===')
ha = c.execute('SELECT COUNT(*) as c FROM memory_affect').fetchone()['c']
ta = c.execute('SELECT COUNT(*) as c FROM memories').fetchone()['c']
print(f'  With affect: {ha} / {ta} ({round(ha/max(1,ta)*100,1)}%)')

print('\n=== DURABLE TOTAL ===')
td = c.execute('SELECT COUNT(*) as c FROM memories WHERE status="durable"').fetchone()['c']
print(f'  {td}')

c.close()
