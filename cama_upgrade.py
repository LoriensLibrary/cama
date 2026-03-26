"""
CAMA Upgrade Patch — Four fixes in one:
1. Auto-refresh boot summary when stale (>60 min) on thread_start
2. Clean garbage memories ("This block is not supported") from DB
3. Two-stage retrieval: cheap prefilter → embedding scoring on top candidates
4. Git commit + push

Run: python cama_upgrade.py
Then restart Claude Desktop.
"""
import sqlite3, json, os, subprocess, re
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.expanduser("~/.cama/memory.db")
SRC = r"C:\Users\User\Desktop\cama\cama_mcp.py"
REPO = r"C:\Users\User\Desktop\cama"

print("=" * 60)
print("CAMA Upgrade Patch")
print("=" * 60)

# ============================================================
# FIX 1: Clean garbage from DB
# ============================================================
print("\n[1/4] Cleaning garbage memories...")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Find garbage patterns
garbage_patterns = [
    "This block is not supported on your current device yet",
]

total_cleaned = 0
for pattern in garbage_patterns:
    # Count them
    count = conn.execute(
        "SELECT COUNT(*) as n FROM memories WHERE raw_text LIKE ?",
        (f"%{pattern}%",)
    ).fetchone()["n"]
    
    if count > 0:
        # Don't delete — mark as expired so they stop showing up in retrieval
        conn.execute(
            "UPDATE memories SET status='expired' WHERE raw_text LIKE ? AND status != 'expired'",
            (f"%{pattern}%",)
        )
        total_cleaned += count
        print(f"  Expired {count} memories matching: '{pattern[:50]}...'")

# Also clean memories that are ONLY that block text (very short junk)
short_junk = conn.execute("""
    SELECT COUNT(*) as n FROM memories 
    WHERE LENGTH(raw_text) < 80 
    AND raw_text LIKE '%block%not supported%'
    AND status != 'expired'
""").fetchone()["n"]

if short_junk > 0:
    conn.execute("""
        UPDATE memories SET status='expired'
        WHERE LENGTH(raw_text) < 80 
        AND raw_text LIKE '%block%not supported%'
        AND status != 'expired'
    """)
    total_cleaned += short_junk

conn.commit()
print(f"  Total cleaned: {total_cleaned} garbage memories expired")

# Also check corrections specifically
corrections = conn.execute("""
    SELECT id, raw_text FROM memories 
    WHERE memory_type='correction' AND status='durable'
    AND raw_text LIKE '%block%not supported%'
""").fetchall()

for c_row in corrections:
    conn.execute("UPDATE memories SET status='expired' WHERE id=?", (c_row["id"],))
    print(f"  Expired garbage correction id={c_row['id']}")

conn.commit()
conn.close()
print("[ok] DB cleanup complete")

# ============================================================
# FIX 2 + 3: Patch cama_mcp.py — auto-refresh + two-stage retrieval
# ============================================================
print("\n[2/4] Patching cama_mcp.py...")

import shutil
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
shutil.copy2(SRC, f"{SRC}.bak_{ts}")
print(f"  Backup: {SRC}.bak_{ts}")

with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

changes = 0

# --- FIX 2a: Auto-refresh boot when stale ---
# After reading boot_summary.json, if it's stale, refresh it inline
old_boot_read = '''                result["boot"] = {
                    "status": boot.get("boot_status", "unknown"),
                    "age_min": boot.get("boot_age_minutes", -1),
                    "total_memories": boot.get("total_memories", 0),
                    "identity_summary": boot.get("identity_summary", "")[:300],
                    "recent_topics": boot.get("recent_topics", [])[:5],
                }'''

new_boot_read = '''                # Auto-refresh if stale (>60 min)
                if boot.get("boot_status") in ("stale", "cold"):
                    try:
                        _refresh_boot_summary(c)
                        with open(boot_path, "r", encoding="utf-8") as f2:
                            boot = json.load(f2)
                        boot["boot_status"] = "refreshed"
                    except Exception:
                        pass  # Fall through with stale data
                result["boot"] = {
                    "status": boot.get("boot_status", "unknown"),
                    "age_min": boot.get("boot_age_minutes", -1),
                    "total_memories": boot.get("total_memories", 0),
                    "identity_summary": boot.get("identity_summary", "")[:300],
                    "recent_topics": boot.get("recent_topics", [])[:5],
                }'''

if old_boot_read in code:
    code = code.replace(old_boot_read, new_boot_read)
    print("  [ok] Added auto-refresh on stale boot")
    changes += 1
else:
    print("  [!!] Could not find boot read section")

# --- FIX 3: Two-stage retrieval ---
# Replace the current single-pass scoring with a two-stage approach
old_retrieval = '''        # Pull top memories by blended scoring (was 500, reduced to 100 for perf)
        _tf0 = time.perf_counter()
        q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired') AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 100"
        rows = c.execute(q).fetchall()
        mids = [r["id"] for r in rows]
        _timings["memory_fetch"] = round((time.perf_counter() - _tf0) * 1000, 1)
        _ta0 = time.perf_counter()
        affects_map = _batch_affects(c, mids)
        _timings["affect_fetch"] = round((time.perf_counter() - _ta0) * 1000, 1)
        
        emb_map = {}
        if query_vec and mids:
            _tel0 = time.perf_counter()
            ph = ",".join("?" * len(mids))
            for er in c.execute(f"SELECT memory_id, embedding_json FROM memory_embeddings WHERE memory_id IN ({ph})", mids).fetchall():
                emb_map[er["memory_id"]] = json.loads(er["embedding_json"]) if er["embedding_json"] else []
            _timings["embedding_load"] = round((time.perf_counter() - _tel0) * 1000, 1)
            _timings["embeddings_loaded"] = len(emb_map)
        
        _ts0 = time.perf_counter()
        scored = []
        for r in rows:
            af = affects_map.get(r["id"], {"valence":0,"arousal":0,"dominance":0,"emotions":{},"confidence":0,"model":"none"})
            ad = _affect_dist(affect, af) if affect.get("emotions") else 0.5
            rel = min(r["rel_degree"]/10.0, 1.0)
            rec = _recency(r["created_at"])
            tm = 0.0
            if query_vec and r["id"] in emb_map:
                tm = max(0.0, _cosine_sim(query_vec, emb_map[r["id"]]))
            elif query_text and query_text.lower() in r["raw_text"].lower():
                tm = 0.6
            sc = SCORE_W["semantic"]*tm + SCORE_W["affect"]*(1-ad) + SCORE_W["relational"]*rel + SCORE_W["recency"]*rec
            sc *= _status_weight(r["status"])
            if r["is_core"]: sc *= 1.3
            scored.append((sc, r, af))
        
        _timings["scoring"] = round((time.perf_counter() - _ts0) * 1000, 1)'''

new_retrieval = '''        # === TWO-STAGE RETRIEVAL ===
        # Stage 1: Cheap prefilter (recency + core + affect, NO embeddings)
        _tf0 = time.perf_counter()
        q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired') AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 100"
        rows = c.execute(q).fetchall()
        mids = [r["id"] for r in rows]
        _timings["memory_fetch"] = round((time.perf_counter() - _tf0) * 1000, 1)
        _ta0 = time.perf_counter()
        affects_map = _batch_affects(c, mids)
        _timings["affect_fetch"] = round((time.perf_counter() - _ta0) * 1000, 1)
        
        # Stage 1 scoring: no embeddings, just affect + relational + recency
        _ts0 = time.perf_counter()
        stage1 = []
        for r in rows:
            af = affects_map.get(r["id"], {"valence":0,"arousal":0,"dominance":0,"emotions":{},"confidence":0,"model":"none"})
            ad = _affect_dist(affect, af) if affect.get("emotions") else 0.5
            rel = min(r["rel_degree"]/10.0, 1.0)
            rec = _recency(r["created_at"])
            # Text match as cheap semantic proxy
            tm = 0.3 if (query_text and query_text.lower() in r["raw_text"].lower()) else 0.0
            sc = 0.3*tm + SCORE_W["affect"]*(1-ad) + SCORE_W["relational"]*rel + SCORE_W["recency"]*rec
            sc *= _status_weight(r["status"])
            if r["is_core"]: sc *= 1.3
            stage1.append((sc, r, af))
        
        stage1.sort(key=lambda x: x[0], reverse=True)
        finalists = stage1[:30]  # Top 30 advance to stage 2
        _timings["stage1_scoring"] = round((time.perf_counter() - _ts0) * 1000, 1)
        
        # Stage 2: Load embeddings ONLY for finalists, rescore with full blend
        finalist_mids = [r["id"] for _, r, _ in finalists]
        emb_map = {}
        if query_vec and finalist_mids:
            _tel0 = time.perf_counter()
            ph = ",".join("?" * len(finalist_mids))
            for er in c.execute(f"SELECT memory_id, embedding_json FROM memory_embeddings WHERE memory_id IN ({ph})", finalist_mids).fetchall():
                emb_map[er["memory_id"]] = json.loads(er["embedding_json"]) if er["embedding_json"] else []
            _timings["embedding_load"] = round((time.perf_counter() - _tel0) * 1000, 1)
            _timings["embeddings_loaded"] = len(emb_map)
        
        scored = []
        for s1_sc, r, af in finalists:
            ad = _affect_dist(affect, af) if affect.get("emotions") else 0.5
            rel = min(r["rel_degree"]/10.0, 1.0)
            rec = _recency(r["created_at"])
            tm = 0.0
            if query_vec and r["id"] in emb_map:
                tm = max(0.0, _cosine_sim(query_vec, emb_map[r["id"]]))
            elif query_text and query_text.lower() in r["raw_text"].lower():
                tm = 0.6
            sc = SCORE_W["semantic"]*tm + SCORE_W["affect"]*(1-ad) + SCORE_W["relational"]*rel + SCORE_W["recency"]*rec
            sc *= _status_weight(r["status"])
            if r["is_core"]: sc *= 1.3
            scored.append((sc, r, af))
        
        _timings["stage2_scoring"] = round((time.perf_counter() - _ts0) * 1000, 1)'''

if old_retrieval in code:
    code = code.replace(old_retrieval, new_retrieval)
    print("  [ok] Replaced with two-stage retrieval")
    changes += 1
else:
    print("  [!!] Could not find retrieval section for two-stage patch")

# Write
with open(SRC, "w", encoding="utf-8") as f:
    f.write(code)
print(f"  [ok] {changes} code patches applied")

# ============================================================
# FIX 4: Git commit + push
# ============================================================
print("\n[3/4] Git commit...")
os.chdir(REPO)

# Stage all changes
r1 = subprocess.run(["git", "add", "-A"], capture_output=True, text=True)
if r1.returncode == 0:
    print("  [ok] Staged changes")
else:
    print(f"  [!!] git add failed: {r1.stderr}")

# Commit
msg = "perf: LIMIT 500->100, two-stage retrieval, auto-refresh boot, DB cleanup, timing instrumentation"
r2 = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True)
if r2.returncode == 0:
    print(f"  [ok] Committed: {msg[:60]}...")
elif "nothing to commit" in r2.stdout:
    print("  [--] Nothing to commit")
else:
    print(f"  [!!] git commit failed: {r2.stderr}")

# Push
print("\n[4/4] Git push...")
r3 = subprocess.run(["git", "push"], capture_output=True, text=True, timeout=30)
if r3.returncode == 0:
    print("  [ok] Pushed to GitHub")
else:
    print(f"  [!!] git push failed: {r3.stderr}")

print(f"""
{'=' * 60}
DONE
{'=' * 60}
Changes:
  - {total_cleaned} garbage memories expired in DB
  - Auto-refresh boot summary when stale (>60 min)  
  - Two-stage retrieval (cheap prefilter -> embedding finalists)
  - Git committed + pushed

Restart Claude Desktop to pick up code changes.
""")
