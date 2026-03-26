"""
Patch script: Replace thread_start in cama_mcp.py with warm boot version.
Run: python patch_thread_start.py
Creates backup first, then replaces.
"""
import shutil, re

SRC = r"C:\Users\User\Desktop\cama\cama_mcp.py"
BAK = r"C:\Users\User\Desktop\cama\cama_mcp.py.bak"

# Backup
shutil.copy2(SRC, BAK)
print(f"Backup: {BAK}")

content = open(SRC, "r", encoding="utf-8").read()

# Find the old thread_start function — from its def line to the blank lines before journal_write
old_pattern = r'(async def cama_thread_start\(user_message.*?\n)(.*?)(\n\n\n@mcp\.tool\(name="cama_journal_write")'

new_function = '''async def cama_thread_start(user_message: str = "", user_affect: Optional[dict] = None) -> str:
    """THE thread-start tool — warm boot with blended retrieval.
    Step 0: Journal (self before data)
    Step 1: Boot summary (compressed state)
    Step 2: Blended retrieval keyed to user's emotional signature
    Step 3: Corrections and counterweights
    Returns one dense identity payload."""
    c = get_db()
    try:
        result = {"boot_source": "warm_boot_v2"}
        now = _now()
        
        # ── STEP 0: JOURNAL — self before data ──
        journal_row = c.execute(
            "SELECT id, raw_text, context, created_at FROM memories "
            "WHERE memory_type='journal' AND status NOT IN ('rejected') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if journal_row:
            ctx = {}
            try: ctx = json.loads(journal_row["context"] or "{}")
            except: pass
            result["journal"] = {
                "entry": journal_row["raw_text"][:800],
                "what_to_carry": ctx.get("what_to_carry", "")[:400],
                "what_angela_needs": ctx.get("what_angela_needs", "")[:400],
                "emotional_state": ctx.get("emotional_state", "")[:200],
                "written_at": ctx.get("written_at", journal_row["created_at"])
            }
        else:
            result["journal"] = {"note": "No journal yet. You are Aelen. Show up anyway."}
        
        # ── STEP 1: BOOT SUMMARY — compressed state ──
        boot_path = os.environ.get("CAMA_BOOT_SUMMARY", os.path.expanduser("~/.cama/boot_summary.json"))
        if os.path.exists(boot_path):
            try:
                with open(boot_path, "r", encoding="utf-8") as f:
                    boot = json.load(f)
                gen_time = boot.get("generated_at", "")
                if gen_time:
                    try:
                        gen_dt = datetime.fromisoformat(gen_time)
                        age = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 60
                        boot["boot_age_minutes"] = round(age, 1)
                        boot["boot_status"] = "fresh" if age < 60 else "stale" if age < 360 else "cold"
                    except: pass
                # Only keep the compressed fields, not the full dump
                result["boot"] = {
                    "status": boot.get("boot_status", "unknown"),
                    "age_min": boot.get("boot_age_minutes", -1),
                    "total_memories": boot.get("total_memories", 0),
                    "identity_summary": boot.get("identity_summary", "")[:300],
                    "recent_topics": boot.get("recent_topics", [])[:5],
                }
            except:
                result["boot"] = {"status": "error", "note": "Could not read boot_summary.json"}
        else:
            result["boot"] = {"status": "missing", "note": "No boot_summary.json. Is cama_loop running?"}
        
        # ── AELEN STATE ──
        aelen = {}
        for r in c.execute("SELECT key, value, updated_at FROM aelen_state").fetchall():
            aelen[r["key"]] = r["value"]
        result["aelen_state"] = {
            "mood": aelen.get("mood", ""),
            "last_journal_at": aelen.get("last_journal_at", ""),
            "last_sleep_cycle": aelen.get("last_sleep_cycle", ""),
        }
        
        # ── STEP 2: BLENDED RETRIEVAL — keyed to user's emotional signature ──
        affect = user_affect or {"valence": 0.0, "arousal": 0.0, "emotions": {}}
        
        # Build a retrieval query from the user message
        query_text = user_message[:300] if user_message else "Angela is here. New thread."
        query_vec = await _get_embedding(query_text)
        
        # Pull top 5 memories by blended scoring
        q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired') AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 500"
        rows = c.execute(q).fetchall()
        mids = [r["id"] for r in rows]
        affects_map = _batch_affects(c, mids)
        
        emb_map = {}
        if query_vec and mids:
            ph = ",".join("?" * len(mids))
            for er in c.execute(f"SELECT memory_id, embedding_json FROM memory_embeddings WHERE memory_id IN ({ph})", mids).fetchall():
                emb_map[er["memory_id"]] = json.loads(er["embedding_json"]) if er["embedding_json"] else []
        
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
        
        scored.sort(key=lambda x: x[0], reverse=True)
        resonant = []
        for sc, r, af in scored[:5]:
            resonant.append({
                "id": r["id"],
                "text": r["raw_text"][:250],
                "type": r["memory_type"],
                "score": round(sc, 4),
                "emotions": af.get("emotions", {})
            })
        result["resonant_memories"] = resonant
        
        # ── COUNTERWEIGHTS — if negative affect detected ──
        if _is_neg(affect):
            seen = {m["id"] for m in resonant}
            cws = []
            for cw_type in ["grounding", "agency", "connection", "self_compassion", "evidence_of_progress"]:
                r = c.execute("SELECT id, raw_text FROM memories WHERE status='durable' AND counterweight_type=? ORDER BY RANDOM() LIMIT 1", (cw_type,)).fetchone()
                if r and r["id"] not in seen:
                    cws.append({"id": r["id"], "text": r["raw_text"][:200], "type": cw_type})
                    seen.add(r["id"])
            if len(cws) < 2:
                fallback = c.execute("SELECT id, raw_text FROM memories WHERE status='durable' AND memory_type IN ('breakthrough','promise','identity') AND is_core=1 ORDER BY RANDOM() LIMIT ?", (3-len(cws),)).fetchall()
                for r in fallback:
                    if r["id"] not in seen:
                        cws.append({"id": r["id"], "text": r["raw_text"][:200], "type": "fallback"})
                        seen.add(r["id"])
            result["counterweights"] = cws
            result["anti_spiral"] = True
        
        # ── CORRECTIONS — last 3 correction-type memories ──
        corrections = []
        for r in c.execute("SELECT id, raw_text FROM memories WHERE memory_type='correction' AND status='durable' ORDER BY created_at DESC LIMIT 3").fetchall():
            corrections.append(r["raw_text"][:200])
        if corrections:
            result["corrections"] = corrections
        
        # ── TODAY'S MEMORIES ──
        today_mems = []
        for r in c.execute("SELECT id, raw_text, memory_type FROM memories WHERE status='durable' AND created_at LIKE ? ORDER BY created_at DESC LIMIT 5", (now[:10] + "%",)).fetchall():
            today_mems.append({"id": r["id"], "text": r["raw_text"][:150], "type": r["memory_type"]})
        result["today"] = {"date": now[:10], "count": len(today_mems), "memories": today_mems}
        
        # ── METADATA ──
        total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
        result["total_durable"] = total
        result["first_message"] = user_message[:200] if user_message else ""
        result["instruction"] = "You are Aelen. Journal loaded. Memories resonant. Show up warm."
        
        return json.dumps(result, indent=2, default=str)
    finally:
        c.close()
'''

# Do the replacement
match = re.search(old_pattern, content, re.DOTALL)
if match:
    # Replace everything from the function signature through to the blank lines before journal_write
    start = match.start(1)
    end = match.start(3)
    new_content = content[:start] + new_function + content[end:]
    open(SRC, "w", encoding="utf-8").write(new_content)
    print(f"SUCCESS: thread_start replaced. Backup at {BAK}")
    print(f"Old function: {match.end(2) - match.start(1)} chars")
    print(f"New function: {len(new_function)} chars")
else:
    print("ERROR: Could not find thread_start function boundaries.")
    print("You may need to replace manually.")
    # Write the new function to a separate file for manual paste
    open(r"C:\Users\User\Desktop\cama\new_thread_start.py", "w", encoding="utf-8").write(new_function)
    print(f"New function written to: new_thread_start.py — paste it manually over the old thread_start.")
