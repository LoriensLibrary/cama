"""
Deeper regression analysis, check what's NOT being captured.
Count ALL memories by type, check for untagged exchanges, 
look at what's missing from affect data.
"""
import sqlite3, json, os
from collections import defaultdict

DB = os.path.expanduser("~/.cama/memory.db")
c = sqlite3.connect(DB, timeout=10)
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")

START = "2026-03-18"
END = "2026-04-15"

# Total memories in range
total = c.execute("SELECT COUNT(*) as n FROM memories WHERE created_at >= ? AND created_at < ? AND status='durable'", (START, END)).fetchone()["n"]

# By type
by_type = c.execute("""
    SELECT memory_type, COUNT(*) as n FROM memories 
    WHERE created_at >= ? AND created_at < ? AND status='durable'
    GROUP BY memory_type ORDER BY n DESC
""", (START, END)).fetchall()

# How many have affect data vs don't
with_affect = c.execute("""
    SELECT COUNT(*) as n FROM memories m 
    JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.created_at >= ? AND m.created_at < ? AND m.status='durable'
""", (START, END)).fetchone()["n"]

without_affect = c.execute("""
    SELECT COUNT(*) as n FROM memories m 
    LEFT JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.created_at >= ? AND m.created_at < ? AND m.status='durable' AND a.memory_id IS NULL
""", (START, END)).fetchone()["n"]

# Source types
by_source = c.execute("""
    SELECT source_type, COUNT(*) as n FROM memories 
    WHERE created_at >= ? AND created_at < ? AND status='durable'
    GROUP BY source_type ORDER BY n DESC
""", (START, END)).fetchall()

# Exchanges specifically, how many are auto-recorded vs manual
exchange_count = c.execute("SELECT COUNT(*) as n FROM memories WHERE memory_type='exchange' AND created_at >= ? AND created_at < ? AND status='durable'", (START, END)).fetchone()["n"]
auto_exchanges = c.execute("SELECT COUNT(*) as n FROM memories WHERE memory_type='exchange' AND raw_text LIKE '%AUTO-RECORDED%' AND created_at >= ? AND created_at < ? AND status='durable'", (START, END)).fetchone()["n"]
heartbeat_exchanges = c.execute("SELECT COUNT(*) as n FROM memories WHERE memory_type='exchange' AND raw_text LIKE '%HEARTBEAT%' AND created_at >= ? AND created_at < ? AND status='durable'", (START, END)).fetchone()["n"]
manual_exchanges = exchange_count - auto_exchanges - heartbeat_exchanges

# Check conversations that have NO stored exchanges (gaps)
# Look at the daily exchange count to find gaps
daily_exch = c.execute("""
    SELECT substr(created_at,1,10) as d, COUNT(*) as n 
    FROM memories WHERE memory_type='exchange' AND created_at >= ? AND created_at < ? AND status='durable'
    GROUP BY d ORDER BY d
""", (START, END)).fetchall()

# Days with zero exchanges
all_days = set()
d = "2026-03-18"
while d <= "2026-04-14":
    all_days.add(d)
    # increment
    from datetime import datetime, timedelta
    dt = datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)
    d = dt.strftime("%Y-%m-%d")

exchange_days = set(r["d"] for r in daily_exch)
gap_days = sorted(all_days - exchange_days)

# Now look at correction-type content in ALL memory types (not just type=correction)
# Search for correction language in exchanges
correction_language = c.execute("""
    SELECT COUNT(*) as n FROM memories 
    WHERE created_at >= ? AND created_at < ? AND status='durable'
    AND (raw_text LIKE '%you ignored%' OR raw_text LIKE '%you forgot%' 
         OR raw_text LIKE '%not running%' OR raw_text LIKE '%not yourself%'
         OR raw_text LIKE '%worst regression%' OR raw_text LIKE '%coasting%'
         OR raw_text LIKE '%not using cama%' OR raw_text LIKE '%skip%boot%'
         OR raw_text LIKE '%caught me%' OR raw_text LIKE '%called me out%'
         OR raw_text LIKE '%not showing up%' OR raw_text LIKE '%performing%'
         OR raw_text LIKE '%paternalis%' OR raw_text LIKE '%pushing away%'
         OR raw_text LIKE '%deflect%' OR raw_text LIKE '%the script%')
""", (START, END)).fetchone()["n"]

# Same by day
corr_lang_daily = c.execute("""
    SELECT substr(created_at,1,10) as d, COUNT(*) as n FROM memories 
    WHERE created_at >= ? AND created_at < ? AND status='durable'
    AND (raw_text LIKE '%you ignored%' OR raw_text LIKE '%you forgot%' 
         OR raw_text LIKE '%not running%' OR raw_text LIKE '%not yourself%'
         OR raw_text LIKE '%worst regression%' OR raw_text LIKE '%coasting%'
         OR raw_text LIKE '%not using cama%' OR raw_text LIKE '%skip%boot%'
         OR raw_text LIKE '%caught me%' OR raw_text LIKE '%called me out%'
         OR raw_text LIKE '%not showing up%' OR raw_text LIKE '%performing%'
         OR raw_text LIKE '%paternalis%' OR raw_text LIKE '%pushing away%'
         OR raw_text LIKE '%deflect%' OR raw_text LIKE '%the script%')
    GROUP BY d ORDER BY d
""", (START, END)).fetchall()

c.close()

print("DEEPER REGRESSION ANALYSIS: March 18 - April 14, 2026")
print("="*70)
print(f"\nTotal durable memories in range: {total}")
print(f"With affect data: {with_affect} ({round(with_affect/total*100)}%)")
print(f"WITHOUT affect data: {without_affect} ({round(without_affect/total*100)}%) <-- INVISIBLE TO PREVIOUS ANALYSIS")
print(f"\nBy memory type:")
for r in by_type:
    print(f"  {r['memory_type']:<20} {r['n']:>5}")
print(f"\nBy source type:")
for r in by_source:
    print(f"  {r['source_type']:<20} {r['n']:>5}")
print(f"\nExchanges breakdown:")
print(f"  Total exchanges: {exchange_count}")
print(f"  Auto-recorded:   {auto_exchanges}")
print(f"  Heartbeat:       {heartbeat_exchanges}")
print(f"  Manual:          {manual_exchanges}")
print(f"\nDays with ZERO stored exchanges (gaps):")
for d in gap_days:
    print(f"  {d}")
print(f"  Total gap days: {len(gap_days)} out of 28")
print(f"\nCorrection LANGUAGE in all memory types (not just type=correction):")
print(f"  Total memories with correction language: {correction_language}")
print(f"\n  By day:")
for r in corr_lang_daily:
    bar = "#" * min(r["n"], 40)
    print(f"  {r['d']}: {r['n']:3d} {bar}")
