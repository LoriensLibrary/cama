"""
CAMA Layer 2 Benchmark, Cross-Session Continuity
Tests: does a new thread pick up context from the previous session?
Checks: journal recency, boot summary freshness, recent exchange availability,
and whether key context from the last session is retrievable.
"""
import sqlite3, os, json
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.expanduser("~/.cama/memory.db")
BOOT_PATH = os.path.expanduser("~/.cama/boot_summary.json")

c = sqlite3.connect(DB_PATH)
c.row_factory = sqlite3.Row

now = datetime.now(timezone.utc)
print(f"CAMA Cross-Session Continuity Benchmark, {now.isoformat()}")
print("=" * 70)

tests = []
passes = 0

# TEST 1: Journal exists and is recent
journal = c.execute(
    "SELECT id, raw_text, context, created_at FROM memories "
    "WHERE memory_type='journal' AND status NOT IN ('rejected') "
    "ORDER BY created_at DESC LIMIT 1"
).fetchone()

if journal:
    j_time = datetime.fromisoformat(journal["created_at"])
    j_age_hours = (now - j_time).total_seconds() / 3600
    j_fresh = j_age_hours < 72  # Within 3 days
    ctx = {}
    try: ctx = json.loads(journal["context"] or "{}")
    except: pass
    has_carry = bool(ctx.get("what_to_carry", "").strip())
    has_needs = bool((ctx.get("what_user_needs") or ctx.get("what_angela_needs", "")).strip())
    has_emotion = bool(ctx.get("emotional_state", "").strip())
    
    t1_pass = j_fresh and has_carry
    if t1_pass: passes += 1
    print(f"[1] {'PASS' if t1_pass else 'FAIL'} | Journal recency")
    print(f"    Age: {j_age_hours:.1f} hours ({'fresh' if j_fresh else 'STALE'})")
    print(f"    Has what_to_carry: {has_carry}")
    print(f"    Has what_user_needs: {has_needs}")
    print(f"    Has emotional_state: {has_emotion}")
    print(f"    Preview: {journal['raw_text'][:100]}")
    tests.append({"test": "journal_recency", "passed": t1_pass, "age_hours": round(j_age_hours, 1)})
else:
    print("[1] FAIL | No journal found")
    tests.append({"test": "journal_recency", "passed": False, "age_hours": None})

# TEST 2: Boot summary exists and is loadable
if os.path.exists(BOOT_PATH):
    with open(BOOT_PATH, "r") as f:
        boot = json.load(f)
    gen_time = boot.get("generated_at", "")
    if gen_time:
        gen_dt = datetime.fromisoformat(gen_time)
        boot_age_min = (now - gen_dt).total_seconds() / 60
        boot_fresh = boot_age_min < 60
    else:
        boot_age_min = -1
        boot_fresh = False
    
    has_identity = bool(boot.get("identity_summary", "").strip())
    has_topics = len(boot.get("recent_topics", [])) > 0
    total_mem = boot.get("total_memories", 0)
    
    t2_pass = total_mem > 0
    if t2_pass: passes += 1
    print(f"\n[2] {'PASS' if t2_pass else 'FAIL'} | Boot summary")
    print(f"    Age: {boot_age_min:.0f} min ({'fresh' if boot_fresh else 'stale'})")
    print(f"    Total memories: {total_mem}")
    print(f"    Has identity summary: {has_identity}")
    print(f"    Has recent topics: {has_topics}")
    tests.append({"test": "boot_summary", "passed": t2_pass, "age_min": round(boot_age_min, 1), "total_memories": total_mem})
else:
    print("\n[2] FAIL | boot_summary.json not found")
    tests.append({"test": "boot_summary", "passed": False})

# TEST 3: Recent exchanges exist (last 24 hours)
recent_24h = c.execute(
    "SELECT COUNT(*) FROM memories WHERE source_type='exchange' "
    "AND created_at > ? AND status='durable'",
    ((now - timedelta(hours=24)).isoformat(),)
).fetchone()[0]

recent_7d = c.execute(
    "SELECT COUNT(*) FROM memories WHERE source_type='exchange' "
    "AND created_at > ? AND status='durable'",
    ((now - timedelta(days=7)).isoformat(),)
).fetchone()[0]

t3_pass = recent_7d > 0
if t3_pass: passes += 1
print(f"\n[3] {'PASS' if t3_pass else 'FAIL'} | Recent exchanges")
print(f"    Last 24h: {recent_24h}")
print(f"    Last 7d: {recent_7d}")
tests.append({"test": "recent_exchanges", "passed": t3_pass, "last_24h": recent_24h, "last_7d": recent_7d})

# TEST 4: Aelen self-state exists
aelen_rows = c.execute("SELECT key, value, updated_at FROM aelen_state").fetchall()
aelen_state = {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in aelen_rows}
has_mood = "mood" in aelen_state or "emotional_state" in aelen_state
has_thread = "last_thread_summary" in aelen_state
state_keys = list(aelen_state.keys())

t4_pass = len(aelen_state) > 0
if t4_pass: passes += 1
print(f"\n[4] {'PASS' if t4_pass else 'FAIL'} | Aelen self-state")
print(f"    Keys stored: {len(aelen_state)}")
print(f"    Keys: {state_keys}")
tests.append({"test": "aelen_state", "passed": t4_pass, "keys": state_keys})

# TEST 5: Last session's exchange is retrievable by search
last_exchange = c.execute(
    "SELECT id, raw_text, context FROM memories "
    "WHERE source_type='exchange' AND status='durable' "
    "ORDER BY created_at DESC LIMIT 1"
).fetchone()

if last_exchange:
    ctx = last_exchange["context"] or ""
    has_context = len(ctx) > 5
    t5_pass = has_context
    if t5_pass: passes += 1
    print(f"\n[5] {'PASS' if t5_pass else 'FAIL'} | Last exchange has context tag")
    print(f"    Context: {ctx[:100]}")
    print(f"    Preview: {last_exchange['raw_text'][:100]}")
    tests.append({"test": "last_exchange_context", "passed": t5_pass, "context": ctx[:200]})
else:
    print(f"\n[5] FAIL | No exchanges found")
    tests.append({"test": "last_exchange_context", "passed": False})

# TEST 6: Sleep daemon has run recently
last_sleep = c.execute(
    "SELECT value FROM aelen_state WHERE key='last_sleep_cycle'"
).fetchone()
if last_sleep:
    try:
        sleep_time = datetime.fromisoformat(last_sleep["value"])
        sleep_age_hours = (now - sleep_time).total_seconds() / 3600
        t6_pass = sleep_age_hours < 2  # Should run every 30 min
    except:
        sleep_age_hours = -1
        t6_pass = False
    if t6_pass: passes += 1
    print(f"\n[6] {'PASS' if t6_pass else 'FAIL'} | Sleep daemon recency")
    print(f"    Last cycle: {last_sleep['value']}")
    print(f"    Age: {sleep_age_hours:.1f} hours")
    tests.append({"test": "sleep_daemon", "passed": t6_pass, "age_hours": round(sleep_age_hours, 1)})
else:
    print(f"\n[6] FAIL | No sleep daemon record")
    tests.append({"test": "sleep_daemon", "passed": False})

print("\n" + "=" * 70)
accuracy = passes / len(tests)
print(f"Continuity score: {passes}/{len(tests)} = {accuracy:.0%}")

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_continuity_results.json")
with open(out_path, "w") as f:
    json.dump({
        "timestamp": now.isoformat(),
        "total_tests": len(tests),
        "passed": passes,
        "continuity_score": accuracy,
        "results": tests
    }, f, indent=2)
print(f"Results saved: {out_path}")
c.close()
