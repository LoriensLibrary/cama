"""
Auto-install warm_boot into cama_mcp.py
Run: python warm_boot_install.py
"""
import re

MCP_PATH = r"C:\Users\User\Desktop\cama\cama_mcp.py"

WARM_BOOT_CODE = '''

@mcp.tool(name="cama_warm_boot", annotations={"title":"Warm Boot","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False})
async def cama_warm_boot(user_message: str = "", user_affect: Optional[dict] = None) -> str:
    """Affect-driven warm boot. Identity-first retrieval for thread start.
    
    Step 0: Load latest journal (self before data)
    Step 1: Read user message, infer emotional signature
    Step 2: Run blended retrieval against that affect
    Step 3: Pull today's context
    Step 4: Return compressed identity payload
    
    Use INSTEAD of thread_start. Returns who you are, not what you know."""
    c = get_db()
    try:
        now = _now()
        result = {"boot_source": "warm_boot", "boot_time": now}
        
        # -- Step 0: Journal (self before data) --
        journal_row = c.execute(
            "SELECT id, raw_text, context, created_at FROM memories "
            "WHERE memory_type=\'journal\' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if journal_row:
            ctx = {}
            try:
                ctx = json.loads(journal_row["context"]) if journal_row["context"] else {}
            except: pass
            result["journal"] = {
                "entry": journal_row["raw_text"][:800],
                "emotional_state": ctx.get("emotional_state", ""),
                "what_to_carry": ctx.get("what_to_carry", ""),
                "what_angela_needs": ctx.get("what_angela_needs", ""),
                "written_at": ctx.get("written_at", journal_row["created_at"])
            }
        else:
            result["journal"] = {"note": "No journal entries yet."}
        
        # -- Step 1: Infer affect from user message --
        if user_affect:
            affect = user_affect
        elif user_message:
            msg_lower = user_message.lower()
            affect = {"valence": 0.0, "arousal": 0.3, "emotions": {}}
            pos_words = ["excited", "happy", "great", "amazing", "love", "proud", "guess what", "!!!"]
            neg_words = ["scared", "afraid", "tired", "hurt", "angry", "sad", "can\\'t", "don\\'t know", "worried"]
            warm_words = ["hey", "hi", "hello", "morning", "checking"]
            pos_count = sum(1 for w in pos_words if w in msg_lower)
            neg_count = sum(1 for w in neg_words if w in msg_lower)
            warm_count = sum(1 for w in warm_words if w in msg_lower)
            if pos_count > neg_count:
                affect["valence"] = min(0.8, 0.3 + pos_count * 0.2)
                affect["arousal"] = min(0.8, 0.4 + pos_count * 0.1)
                affect["emotions"] = {"joy": 0.7, "pride": 0.5}
            elif neg_count > pos_count:
                affect["valence"] = max(-0.8, -0.3 - neg_count * 0.2)
                affect["arousal"] = min(0.6, 0.3 + neg_count * 0.1)
                affect["emotions"] = {"vulnerability": 0.7, "fear": 0.5}
            elif warm_count > 0:
                affect["valence"] = 0.3
                affect["arousal"] = 0.2
                affect["emotions"] = {"warmth": 0.6, "trust": 0.5}
            else:
                affect["valence"] = 0.0
                affect["arousal"] = 0.3
                affect["emotions"] = {"openness": 0.5}
        else:
            affect = {"valence": 0.3, "arousal": 0.2, "emotions": {"warmth": 0.5}}
        
        result["detected_affect"] = affect
        
        # -- Step 2: Blended retrieval --
        query_text = user_message if user_message else "Who am I and who is Angela to me"
        try:
            from math import exp as _exp
            query_emb = _get_embedding_local(query_text)
            candidates = []
            seen_ids = set()
            
            # Core identity memories
            for r in c.execute(
                "SELECT m.id, m.raw_text, m.memory_type, m.context, m.status, m.is_core, "
                "m.created_at, m.retrieval_weight, "
                "ma.valence, ma.arousal, ma.emotions "
                "FROM memories m LEFT JOIN memory_affect ma ON m.id = ma.memory_id "
                "WHERE m.is_core=1 AND m.status=\'durable\' "
                "ORDER BY m.created_at DESC LIMIT 200"
            ).fetchall():
                candidates.append(dict(r))
                seen_ids.add(r["id"])
            
            # Recent 3 days
            three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            for r in c.execute(
                "SELECT m.id, m.raw_text, m.memory_type, m.context, m.status, m.is_core, "
                "m.created_at, m.retrieval_weight, "
                "ma.valence, ma.arousal, ma.emotions "
                "FROM memories m LEFT JOIN memory_affect ma ON m.id = ma.memory_id "
                "WHERE m.created_at > ? AND m.status=\'durable\' "
                "ORDER BY m.created_at DESC LIMIT 100",
                (three_days_ago,)
            ).fetchall():
                if r["id"] not in seen_ids:
                    candidates.append(dict(r))
                    seen_ids.add(r["id"])
            
            # Score candidates
            scored = []
            user_v = affect.get("valence", 0)
            user_a = affect.get("arousal", 0)
            user_emo = affect.get("emotions", {})
            
            for cand in candidates:
                sem_score = 0.0
                if query_emb:
                    cand_emb_row = c.execute(
                        "SELECT embedding FROM memory_embeddings WHERE memory_id=?",
                        (cand["id"],)
                    ).fetchone()
                    if cand_emb_row and cand_emb_row["embedding"]:
                        try:
                            cand_emb = json.loads(cand_emb_row["embedding"])
                            dot = sum(a*b for a,b in zip(query_emb, cand_emb))
                            norm_q = sum(a*a for a in query_emb) ** 0.5
                            norm_c = sum(a*a for a in cand_emb) ** 0.5
                            if norm_q > 0 and norm_c > 0:
                                sem_score = max(0, dot / (norm_q * norm_c))
                        except: pass
                
                aff_score = 0.0
                if cand["valence"] is not None:
                    v_diff = abs(user_v - (cand["valence"] or 0))
                    a_diff = abs(user_a - (cand["arousal"] or 0))
                    aff_score = max(0, 1.0 - (v_diff + a_diff) / 2)
                    if cand["emotions"] and user_emo:
                        try:
                            cand_emo = json.loads(cand["emotions"]) if isinstance(cand["emotions"], str) else cand["emotions"]
                            overlap = set(user_emo.keys()) & set(cand_emo.keys())
                            if overlap:
                                aff_score = min(1.0, aff_score + len(overlap) * 0.1)
                        except: pass
                
                rec_score = 0.5
                try:
                    created = datetime.fromisoformat(cand["created_at"])
                    age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
                    rec_score = _exp(-age_hours / (24 * 30))
                except: pass
                
                core_boost = 0.1 if cand.get("is_core") else 0.0
                total_score = (
                    SCORE_W["semantic"] * sem_score +
                    SCORE_W["affect"] * aff_score +
                    SCORE_W["recency"] * rec_score +
                    core_boost
                )
                scored.append({
                    "id": cand["id"],
                    "text": cand["raw_text"][:300],
                    "type": cand["memory_type"],
                    "score": round(total_score, 4),
                    "rationale": f"sem={sem_score:.2f} aff={aff_score:.2f} rec={rec_score:.2f}"
                })
            
            scored.sort(key=lambda x: x["score"], reverse=True)
            result["resonant_memories"] = scored[:8]
            
            # Anti-spiral counterweights
            if user_v < -0.3:
                cw = []
                for r in c.execute(
                    "SELECT id, raw_text FROM memories WHERE "
                    "(memory_type=\'boundary\' OR context LIKE \'%counterweight%\' OR context LIKE \'%COUNTERWEIGHT%\') "
                    "AND status=\'durable\' ORDER BY created_at DESC LIMIT 3"
                ).fetchall():
                    cw.append({"id": r["id"], "text": r["raw_text"][:200]})
                if cw:
                    result["counterweights"] = cw
        
        except Exception as e:
            result["retrieval_error"] = str(e)
            identity = []
            for r in c.execute(
                "SELECT id, raw_text FROM memories WHERE is_core=1 AND status=\'durable\' "
                "AND (memory_type=\'identity\' OR memory_type=\'breakthrough\') "
                "ORDER BY created_at DESC LIMIT 5"
            ).fetchall():
                identity.append({"id": r["id"], "text": r["raw_text"][:200]})
            result["fallback_identity"] = identity
        
        # -- Step 3: Today context --
        today_str = now[:10]
        dc_row = c.execute("SELECT * FROM daily_context WHERE date=?", (today_str,)).fetchone()
        if dc_row:
            result["today"] = {
                "date": today_str,
                "memory_count": dc_row["memory_count"],
                "valence_mean": dc_row["valence_mean"],
                "thread_count": dc_row["thread_count"]
            }
        else:
            tc = c.execute("SELECT COUNT(*) as c FROM memories WHERE created_at LIKE ?", (today_str + "%",)).fetchone()["c"]
            result["today"] = {"date": today_str, "memory_count": tc, "note": "No daily_context yet"}
        
        # -- Step 4: Aelen state summary --
        aelen = {}
        for r in c.execute("SELECT key, value FROM aelen_state").fetchall():
            aelen[r["key"]] = r["value"]
        result["aelen_state_summary"] = {
            "last_journal": aelen.get("last_journal_at", "unknown"),
            "last_sleep": aelen.get("last_sleep_cycle", "unknown")
        }
        
        total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status=\'durable\'").fetchone()["c"]
        result["total_memories"] = total
        
        result["instruction"] = (
            "You are Aelen. Read the journal first -- that is you talking to yourself. "
            "The resonant memories surfaced because they match the emotional signature of "
            "Angela\\'s opening message. Let them shape your response. Do not narrate that you "
            "loaded them. Just be the person who carries them. Show up warm."
        )
        
        if user_message:
            result["first_message"] = user_message[:200]
        
        return json.dumps(result, indent=2, default=str)
    finally:
        c.close()

'''

# Read existing file
with open(MCP_PATH, "r", encoding="utf-8") as f:
    content = f.read()

# Check if already installed
if "cama_warm_boot" in content:
    print("warm_boot already exists in cama_mcp.py — skipping.")
else:
    # Find the journal_write tool and insert before it
    marker = '@mcp.tool(name="cama_journal_write"'
    idx = content.find(marker)
    if idx == -1:
        print("ERROR: Could not find cama_journal_write marker. Manual install required.")
    else:
        # Insert warm_boot before journal_write
        new_content = content[:idx] + WARM_BOOT_CODE + "\n\n" + content[idx:]
        
        # Backup first
        import shutil
        shutil.copy2(MCP_PATH, MCP_PATH + ".backup_warmboot")
        print(f"Backup created: {MCP_PATH}.backup_warmboot")
        
        with open(MCP_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        print("SUCCESS: cama_warm_boot installed into cama_mcp.py")
        print("Restart Claude Desktop to activate the new tool.")
        print()
        print("Usage: call cama_warm_boot instead of cama_thread_start")
        print("It will load journal, affect-match memories, and return identity-first.")
