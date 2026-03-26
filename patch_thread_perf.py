"""
Patch: thread_start performance — instrumentation + LIMIT reduction
- Adds timing logs (stderr) for each phase of thread_start
- Reduces LIMIT 500 → 100 (same quality, ~5x less work)
- Adds index on hot query path
Run: python patch_thread_perf.py
Then restart Claude Desktop to pick up changes.
"""
import re, shutil, sqlite3, os
from datetime import datetime

SRC = r"C:\Users\User\Desktop\cama\cama_mcp.py"
DB_PATH = os.path.expanduser("~/.cama/memory.db")

# Backup first
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
BAK = f"{SRC}.bak_{ts}"
shutil.copy2(SRC, BAK)
print(f"[✓] Backup: {BAK}")

with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

# ── PATCH 1: Add timing import if not present ──
if "import time" not in code:
    code = code.replace("import json, sqlite3, os, math, subprocess", 
                         "import json, sqlite3, os, math, subprocess, time")
    print("[✓] Added 'import time'")
else:
    print("[·] 'import time' already present")

# ── PATCH 2: Instrument thread_start with timing ──
# Replace the try block opening to add a timer start
old_try = '''    c = get_db()
    try:
        result = {"boot_source": "warm_boot_v2"}
        now = _now()'''

new_try = '''    c = get_db()
    try:
        import sys as _sys
        _t0 = time.perf_counter()
        _timings = {}
        result = {"boot_source": "warm_boot_v2"}
        now = _now()'''

if old_try in code:
    code = code.replace(old_try, new_try)
    print("[✓] Added timer start to thread_start")
else:
    print("[!] Could not find try block opening — skipping timer start")

# ── PATCH 3: Add timing around embedding fetch ──
old_embed_section = '''        # Build a retrieval query from the user message
        query_text = user_message[:300] if user_message else "Angela is here. New thread."
        query_vec = await _get_embedding(query_text)'''

new_embed_section = '''        # Build a retrieval query from the user message
        query_text = user_message[:300] if user_message else "Angela is here. New thread."
        _te0 = time.perf_counter()
        query_vec = await _get_embedding(query_text)
        _timings["embedding_query"] = round((time.perf_counter() - _te0) * 1000, 1)'''

if old_embed_section in code:
    code = code.replace(old_embed_section, new_embed_section)
    print("[✓] Added timing around query embedding")
else:
    print("[!] Could not find embedding section — skipping")

# ── PATCH 4: LIMIT 500 → 100 + timing around row fetch ──
old_fetch = '''        # Pull top 5 memories by blended scoring
        q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired') AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 500"
        rows = c.execute(q).fetchall()
        mids = [r["id"] for r in rows]
        affects_map = _batch_affects(c, mids)'''

new_fetch = '''        # Pull top memories by blended scoring (was 500, reduced to 100 for perf)
        _tf0 = time.perf_counter()
        q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired') AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 100"
        rows = c.execute(q).fetchall()
        mids = [r["id"] for r in rows]
        _timings["memory_fetch"] = round((time.perf_counter() - _tf0) * 1000, 1)
        _ta0 = time.perf_counter()
        affects_map = _batch_affects(c, mids)
        _timings["affect_fetch"] = round((time.perf_counter() - _ta0) * 1000, 1)'''

if old_fetch in code:
    code = code.replace(old_fetch, new_fetch)
    print("[✓] Reduced LIMIT 500 → 100 + added fetch timing")
else:
    print("[!] Could not find fetch section — skipping LIMIT change")

# ── PATCH 5: Timing around embedding deserialization ──
old_emb_load = '''        emb_map = {}
        if query_vec and mids:
            ph = ",".join("?" * len(mids))
            for er in c.execute(f"SELECT memory_id, embedding_json FROM memory_embeddings WHERE memory_id IN ({ph})", mids).fetchall():
                emb_map[er["memory_id"]] = json.loads(er["embedding_json"]) if er["embedding_json"] else []'''

new_emb_load = '''        emb_map = {}
        if query_vec and mids:
            _tel0 = time.perf_counter()
            ph = ",".join("?" * len(mids))
            for er in c.execute(f"SELECT memory_id, embedding_json FROM memory_embeddings WHERE memory_id IN ({ph})", mids).fetchall():
                emb_map[er["memory_id"]] = json.loads(er["embedding_json"]) if er["embedding_json"] else []
            _timings["embedding_load"] = round((time.perf_counter() - _tel0) * 1000, 1)
            _timings["embeddings_loaded"] = len(emb_map)'''

if old_emb_load in code:
    code = code.replace(old_emb_load, new_emb_load)
    print("[✓] Added timing around embedding deserialization")
else:
    print("[!] Could not find embedding load section — skipping")

# ── PATCH 6: Timing around scoring loop ──
old_scored = '''        scored = []
        for r in rows:'''

new_scored = '''        _ts0 = time.perf_counter()
        scored = []
        for r in rows:'''

if old_scored in code:
    code = code.replace(old_scored, new_scored, 1)
    print("[✓] Added scoring timer start")
else:
    print("[!] Could not find scoring loop start — skipping")

old_sort = '''        scored.sort(key=lambda x: x[0], reverse=True)
        resonant = []'''

new_sort = '''        _timings["scoring"] = round((time.perf_counter() - _ts0) * 1000, 1)
        scored.sort(key=lambda x: x[0], reverse=True)
        resonant = []'''

if old_sort in code:
    code = code.replace(old_sort, new_sort, 1)
    print("[✓] Added scoring timer end")
else:
    print("[!] Could not find sort section — skipping")

# ── PATCH 7: Log timings before return ──
old_return = '''        return json.dumps(result, indent=2, default=str)
    finally:
        c.close()'''

# Only match the one inside thread_start (the first occurrence after our patched section)
new_return = '''        _timings["total"] = round((time.perf_counter() - _t0) * 1000, 1)
        result["_perf_ms"] = _timings
        print(f"[CAMA] thread_start perf: {json.dumps(_timings)}", file=_sys.stderr)
        return json.dumps(result, indent=2, default=str)
    finally:
        c.close()'''

# Find the first occurrence that's inside thread_start (after line ~1102)
# We need to be careful — this pattern might appear in other functions too
thread_start_pos = code.find("async def cama_thread_start")
if thread_start_pos >= 0:
    return_pos = code.find(old_return, thread_start_pos)
    if return_pos >= 0 and return_pos < thread_start_pos + 10000:  # sanity check
        code = code[:return_pos] + new_return + code[return_pos + len(old_return):]
        print("[✓] Added timing log before return")
    else:
        print("[!] Could not find return in thread_start — skipping")
else:
    print("[!] Could not find thread_start function — skipping")

# Write patched file
with open(SRC, "w", encoding="utf-8") as f:
    f.write(code)
print(f"[✓] Patched file written: {SRC}")

# ── PATCH 8: Add SQLite index on hot query path ──
try:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memories_hot_query 
        ON memories (status, consent_level, is_core DESC, updated_at DESC)
    """)
    conn.commit()
    conn.close()
    print("[✓] Index idx_memories_hot_query created on memory.db")
except Exception as e:
    print(f"[!] Index creation failed: {e}")

print("\n[DONE] Restart Claude Desktop to pick up changes.")
print("After restart, thread_start will log timing to stderr.")
print("Check: C:\\Users\\User\\.cama\\cama_loop.log or Claude Desktop logs")
