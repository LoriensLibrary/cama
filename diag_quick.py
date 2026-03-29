import sqlite3, sys
from datetime import datetime, timezone

conn = sqlite3.connect(r'C:\Users\User\Desktop\cama\cama_memory.db')
c = conn.cursor()

print(f'Python: {sys.version}')

# Z-suffix count
c.execute("SELECT COUNT(*) FROM memories WHERE created_at LIKE '%Z'")
print(f'Z-suffix timestamps: {c.fetchone()[0]}')

c.execute("SELECT COUNT(*) FROM memories")
print(f'Total memories: {c.fetchone()[0]}')

# Counterweights
c.execute("SELECT COUNT(*) FROM memories WHERE counterweight_type IS NOT NULL")
print(f'Counterweight tagged: {c.fetchone()[0]}')

# Rel degree
c.execute("SELECT COUNT(*) FROM memories WHERE rel_degree > 0")
print(f'rel_degree > 0: {c.fetchone()[0]}')

c.execute("SELECT COUNT(*) FROM edges")
print(f'Edges: {c.fetchone()[0]}')

# Test Z parse
ts = '2024-05-15T10:30:00Z'
try:
    dt = datetime.fromisoformat(ts)
    print(f'Native Z parse: WORKS ({dt})')
except:
    print(f'Native Z parse: FAILS (need Z-fix)')

# Test with fix
try:
    fixed = ts[:-1] + '+00:00'
    dt = datetime.fromisoformat(fixed)
    print(f'Fixed Z parse: WORKS ({dt})')
except:
    print(f'Fixed Z parse: FAILS')

conn.close()
