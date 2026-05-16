import sqlite3, math
from datetime import datetime, timezone

conn = sqlite3.connect(r'C:\Users\User\Desktop\cama\cama_memory.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 1. Timestamp analysis
c.execute("SELECT created_at FROM memories LIMIT 5")
print('=== SAMPLE TIMESTAMPS ===')
for r in c.fetchall():
    print(f'  {r[0]}')

c.execute("SELECT COUNT(*) FROM memories WHERE created_at LIKE '%Z'")
z_count = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM memories")
total = c.fetchone()[0]
print(f'Z-suffix: {z_count} / {total} ({100*z_count/total:.1f}%)')

# Non-Z timestamps
c.execute("SELECT COUNT(*) FROM memories WHERE created_at NOT LIKE '%Z'")
non_z = c.fetchone()[0]
print(f'Non-Z: {non_z}')

# 2. Test _parse_t with Z suffix
def parse_t(t):
    if not t: return datetime.now(timezone.utc)
    try:
        if isinstance(t, str) and t.endswith('Z'):
            t = t[:-1] + '+00:00'
        return datetime.fromisoformat(t)
    except:
        return datetime.now(timezone.utc)

def recency(t, half_life_days=30):
    try:
        return math.exp(-math.log(2) * (datetime.now(timezone.utc)-parse_t(t)).total_seconds()/(half_life_days*86400))
    except:
        return 0.5

# Sample recency
c.execute("SELECT created_at FROM memories ORDER BY RANDOM() LIMIT 100")
scores = [recency(r[0]) for r in c.fetchall()]
print(f'\n=== RECENCY SCORES (current _parse_t) ===')
print(f'min={min(scores):.6f}, max={max(scores):.6f}, avg={sum(scores)/len(scores):.6f}')
high = sum(1 for s in scores if s > 0.9)
print(f'Scores > 0.9: {high}/100')

# Now test WITHOUT the Z-fix (to see if the bug still exists in code)
def parse_t_broken(t):
    if not t: return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(t)
    except:
        return datetime.now(timezone.utc)  # Falls back to NOW = recency 1.0

def recency_broken(t, half_life_days=30):
    try:
        return math.exp(-math.log(2) * (datetime.now(timezone.utc)-parse_t_broken(t)).total_seconds()/(half_life_days*86400))
    except:
        return 0.5

c.execute("SELECT created_at FROM memories ORDER BY RANDOM() LIMIT 100")
broken_scores = [recency_broken(r[0]) for r in c.fetchall()]
broken_high = sum(1 for s in broken_scores if s > 0.9)
print(f'\n=== RECENCY WITHOUT Z-FIX (simulated) ===')
print(f'Scores > 0.9: {broken_high}/100 (these would all be 1.0 = broken)')

# Check Python version for fromisoformat Z support
import sys
print(f'\nPython version: {sys.version}')

# 3. Counterweight types
c.execute("SELECT COUNT(*) FROM memories WHERE counterweight_type IS NOT NULL")
cw_count = c.fetchone()[0]
c.execute("SELECT counterweight_type, COUNT(*) FROM memories WHERE counterweight_type IS NOT NULL GROUP BY counterweight_type")
cw_breakdown = c.fetchall()
print(f'\n=== COUNTERWEIGHTS ===')
print(f'Tagged: {cw_count}')
for row in cw_breakdown:
    print(f'  {row[0]}: {row[1]}')

# Candidate counterweight memories (core+positive that COULD be tagged)
c.execute("SELECT COUNT(*) FROM memories WHERE is_core=1 AND status='durable'")
core_durable = c.fetchone()[0]
print(f'Core durable memories (candidates for counterweight tagging): {core_durable}')

# 4. Rel degree
c.execute("SELECT COUNT(*) FROM memories WHERE rel_degree > 0")
rd_count = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM edges")
edge_count = c.fetchone()[0]
print(f'\n=== REL DEGREE ===')
print(f'Memories with rel_degree > 0: {rd_count}')
print(f'Total edges: {edge_count}')

if edge_count > 0:
    c.execute("SELECT from_id, to_id FROM edges LIMIT 5")
    print('Sample edges:')
    for row in c.fetchall():
        f, t = row[0], row[1]
        c2 = conn.cursor()
        c2.execute("SELECT rel_degree FROM memories WHERE id=?", (f,))
        rd = c2.fetchone()
        print(f'  {f} -> {t} (from rel_degree: {rd[0] if rd else "MISSING"})')

# 5. Check what the recompute tool would do
c.execute("SELECT from_id, to_id FROM edges")
all_edges = c.fetchall()
degree_map = {}
for row in all_edges:
    f, t = row[0], row[1]
    degree_map[f] = degree_map.get(f, 0) + 1
    degree_map[t] = degree_map.get(t, 0) + 1
print(f'\nUnique memories referenced in edges: {len(degree_map)}')
if degree_map:
    print(f'Max degree: {max(degree_map.values())}')
    print(f'Avg degree: {sum(degree_map.values())/len(degree_map):.1f}')

conn.close()
