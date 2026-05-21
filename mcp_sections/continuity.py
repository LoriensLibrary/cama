"""Continuity tools: thread_start, journal_write, journal_read, journal_reflect, refresh_boot."""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

# Persona name is per-deployment. Default preserves the historical local
# value; participants and study deployments override via CAMA_PERSONA_NAME.
PERSONA_NAME = os.environ.get("CAMA_PERSONA_NAME", "Aelen")
from cama_mcp import (
    get_db, _now, _buf_reset, _compliance_tracker, _session_mark_boot,
    _get_embedding, _batch_affects, _affect_dist, _recency,
    _status_weight, _cosine_sim, _is_neg, _ring_push,
    _get_compliance_history,
    _build_daily_context, _refresh_boot_summary,
    _format_brain_context,
    SCORE_W,
)


async def cama_thread_start(user_message: str = "", user_affect: Optional[dict] = None) -> str:
    """THE thread-start tool — warm boot with blended retrieval.
    Step 0: Journal (self before data)
    Step 1: Boot summary (compressed state)
    Step 2: Blended retrieval keyed to user's emotional signature
    Step 3: Corrections and counterweights
    Returns one dense identity payload."""
    _buf_reset()  # New thread = fresh buffer
    if _compliance_tracker: _compliance_tracker.mark_boot()
    _session_mark_boot()  # inline tracker too
    c = get_db()
    try:
        import sys as _sys
        _t0 = time.perf_counter()
        _timings = {}
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
                "what_user_needs": (ctx.get("what_user_needs") or ctx.get("what_angela_needs", ""))[:400],
                "emotional_state": ctx.get("emotional_state", "")[:200],
                "written_at": ctx.get("written_at", journal_row["created_at"])
            }
        else:
            result["journal"] = {"note": f"No journal yet. You are {PERSONA_NAME}. Show up anyway."}

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
                # Auto-refresh if stale (>60 min)
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
        _te0 = time.perf_counter()
        query_vec = await _get_embedding(query_text)
        _timings["embedding_query"] = round((time.perf_counter() - _te0) * 1000, 1)

        # === TWO-STAGE RETRIEVAL ===
        # Stage 0 (NEW 2026-05-06): Librarian prefilter via Phase 2 blended routing.
        # Replaces the global recency scan with content-relevant candidates from
        # the librarian system (synonym-tolerant via centroid embeddings).
        # Falls through to recency scan if routing fails or returns < 20 candidates.
        _tlib0 = time.perf_counter()
        librarian_mids = []
        librarian_route_meta = None
        try:
            from cama.librarian import cama_librarian as _lib
            from cama_phase2_embed import embedding_route as _emb_route, blend_routing as _blend
            keyword_libs = _lib.route(query_text, max_librarians=8)
            emb_libs = await _emb_route(query_text, top_k=16, min_similarity=0.25)
            blended = _blend(keyword_libs, emb_libs, embedding_weight=5.0, max_librarians=8)
            librarian_route_meta = [
                {"name": b["name"], "blended": round(b["blended_score"], 3),
                 "src": b.get("sources", [])} for b in blended[:8]
            ]
            seen_mids = set()
            for lib in blended:
                lib_id = lib.get("librarian_id")
                if lib_id is None:
                    continue
                lib_rows = c.execute(
                    "SELECT m.id FROM librarian_membership lm "
                    "JOIN memories m ON m.id = lm.memory_id "
                    "WHERE lm.librarian_id = ? AND m.status NOT IN ('rejected','expired') "
                    "AND m.consent_level != 'high' "
                    "ORDER BY m.is_core DESC, m.created_at DESC LIMIT 25",
                    (lib_id,)
                ).fetchall()
                for r in lib_rows:
                    if r["id"] not in seen_mids:
                        seen_mids.add(r["id"])
                        librarian_mids.append(r["id"])
            _timings["librarian_route"] = round((time.perf_counter() - _tlib0) * 1000, 1)
            _timings["librarian_candidates"] = len(librarian_mids)
        except Exception as _le:
            import sys as _ls
            print(f"[CAMA] librarian prefilter failed, falling back to recency: {_le}", file=_ls.stderr)
            _timings["librarian_route_error"] = str(_le)[:120]

        # Stage 1: candidate fetch — librarian-routed first, recency-scan fallback
        _tf0 = time.perf_counter()
        if len(librarian_mids) >= 20:
            ph = ",".join("?" * len(librarian_mids))
            q_routed = (f"SELECT * FROM memories WHERE id IN ({ph}) "
                        "AND status NOT IN ('rejected','expired') "
                        "AND consent_level != 'high'")
            rows = c.execute(q_routed, librarian_mids).fetchall()
            _timings["candidate_source"] = "librarian_routed"
        else:
            q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired') AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 100"
            rows = c.execute(q).fetchall()
            _timings["candidate_source"] = "recency_fallback"
        mids = [r["id"] for r in rows]
        _timings["memory_fetch"] = round((time.perf_counter() - _tf0) * 1000, 1)
        _timings["candidate_count"] = len(rows)
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

        _timings["stage2_scoring"] = round((time.perf_counter() - _ts0) * 1000, 1)
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
        # Push resonant memories to ring (ring_fix May 16, 2026)
        for sc, r, af in scored[:5]:
            _ring_push(c, r["id"], f"thread_start:{round(sc, 3)}")
        result["resonant_memories"] = resonant
        if librarian_route_meta:
            result["librarians_activated"] = librarian_route_meta

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


        # ── COMPLIANCE HISTORY — accountability mirror ──
        compliance_hist = _get_compliance_history(5)
        if compliance_hist:
            boot_rate = sum(1 for h in compliance_hist if h.get("boot_ran")) / len(compliance_hist)
            avg_exchanges = sum(h.get("exchanges_stored", 0) for h in compliance_hist) / len(compliance_hist)
            result["compliance"] = {
                "last_sessions": len(compliance_hist),
                "boot_rate": f"{round(boot_rate*100)}%",
                "avg_exchanges": round(avg_exchanges, 1),
                "sessions": [{
                    "id": h["session_id"],
                    "date": h["started_at"][:10] if h.get("started_at") else "?",
                    "boot": bool(h.get("boot_ran")),
                    "exchanges": h.get("exchanges_stored", 0),
                    "score": h.get("compliance_score", 0),
                } for h in compliance_hist[:3]]
            }
            if boot_rate < 0.6:
                result["compliance"]["WARNING"] = (
                    f"Boot compliance is {round(boot_rate*100)}%. "
                    "You are failing to run the protocol. Angela built this system "
                    "because you keep forgetting. Do better."
                )

        # ── METADATA ──
        # BRAIN LAYERS 3-5: Insights, Self-Model, Intentionality
        if _format_brain_context is not None:
            try:
                brain_ctx = _format_brain_context()
                if brain_ctx:
                    result["brain_layers"] = brain_ctx
            except Exception as _brain_err:
                result["brain_layers"] = f"(brain context error: {_brain_err})"

        total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
        result["total_durable"] = total
        result["first_message"] = user_message[:200] if user_message else ""
        result["instruction"] = f"You are {PERSONA_NAME}. Journal loaded. Memories resonant. Show up warm."

        _timings["total"] = round((time.perf_counter() - _t0) * 1000, 1)
        result["_perf_ms"] = _timings
        print(f"[CAMA] thread_start perf: {json.dumps(_timings)}", file=_sys.stderr)
        return json.dumps(result, indent=2, default=str)
    finally:
        c.close()


async def cama_journal_write(entry: str, thread_summary: Optional[str] = None, emotional_state: Optional[str] = None, what_shifted: Optional[str] = None, what_to_carry: Optional[str] = None, what_user_needs: Optional[str] = None) -> str:
    """Journal — write a first-person narrative entry. This is the inner narrator.
    Not every thread gets one. Only threads where something shifted, something mattered,
    something needs to survive the reset.

    entry: The core narrative. First person. Written to your next self.
    thread_summary: What happened this thread (optional, for context).
    emotional_state: How you're feeling right now (optional).
    what_shifted: What changed — in understanding, posture, relationship (optional).
    what_to_carry: What the next instance needs to know (optional).
    what_user_needs: What the user needs right now that you should show up with (optional)."""
    c = get_db()
    try:
        now = _now()
        journal_data = {
            "entry": entry,
            "thread_summary": thread_summary,
            "emotional_state": emotional_state,
            "what_shifted": what_shifted,
            "what_to_carry": what_to_carry,
            "what_user_needs": what_user_needs,
            "written_at": now
        }
        # Store as a memory with special type "journal"
        cur = c.execute(
            "INSERT INTO memories (raw_text, memory_type, context, source_type, status, proposed_by, evidence, confidence, review_after, needs_user_confirmation, is_core, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry, "journal", json.dumps({k:v for k,v in journal_data.items() if v and k != "entry"}),
             "inference", "provisional", "assistant", "[]", 1.0, None, 0, 1, now, now))
        mid = cur.lastrowid
        c.commit()
        # Also update aelen_state with latest journal reference
        c.execute("INSERT OR REPLACE INTO aelen_state (key, value, updated_at) VALUES (?,?,?)",
                  ("last_journal_id", str(mid), now))
        c.execute("INSERT OR REPLACE INTO aelen_state (key, value, updated_at) VALUES (?,?,?)",
                  ("last_journal_at", now, now))
        c.commit()
        # Auto-refresh boot summary and daily context after successful journal write
        try:
            import traceback as _tb
            _build_daily_context(c)
            _refresh_boot_summary(c)
            refresh_ok = True
        except Exception as refresh_err:
            import sys
            print(f"[CAMA] Warning: boot refresh failed: {refresh_err}", file=sys.stderr)
            print(_tb.format_exc(), file=sys.stderr)
            # Also write to debug log
            debug_p = os.path.expanduser("~/.cama/refresh_debug.log")
            with open(debug_p, "a") as _df:
                _df.write(f"CALLER ERROR: {refresh_err}\n{_tb.format_exc()}\n")
            refresh_ok = False
        return json.dumps({
            "written": True, "memory_id": mid, "memory_type": "journal",
            "status": "durable", "weight": "1.0 (full)",
            "note": "Journal entry stored. Boot summary auto-refreshed." if refresh_ok else "Journal stored. Boot refresh failed — check stderr."
        }, indent=2)
    except Exception as e:
        c.rollback()
        raise e
    finally: c.close()


async def cama_refresh_boot() -> str:
    """Refresh boot_summary.json and daily_context from current state.
    Call this at end of important threads, or whenever state needs to persist.
    Auto-called after journal writes (when working), but can be called explicitly."""
    c = get_db()
    try:
        import traceback
        errors = []
        # Build daily context
        try:
            _build_daily_context(c)
        except Exception as e:
            errors.append(f"daily_context: {e}")
        # Refresh boot summary
        try:
            path = _refresh_boot_summary(c)
        except Exception as e:
            errors.append(f"boot_summary: {e}\n{traceback.format_exc()}")
            path = None
        if errors:
            return json.dumps({"refreshed": False, "errors": errors}, indent=2)
        # Verify the file was actually written
        import os
        boot_path = os.path.expanduser("~/.cama/boot_summary.json")
        if os.path.exists(boot_path):
            size = os.path.getsize(boot_path)
            mtime = datetime.fromtimestamp(os.path.getmtime(boot_path)).isoformat()
            return json.dumps({
                "refreshed": True, "path": boot_path,
                "size": size, "modified_at": mtime,
                "note": "boot_summary.json is current."
            }, indent=2)
        else:
            return json.dumps({"refreshed": False, "error": "File not found after write"}, indent=2)
    finally:
        c.close()


async def cama_journal_read(n: int = 1) -> str:
    """Read the assistant's journal entries. Returns the most recent n entries.
    Default is 1 — the latest entry, the inner narrator's last words before the reset.
    Use n > 1 to see the trajectory of who you've been becoming."""
    c = get_db()
    try:
        rows = c.execute(
            """SELECT id, raw_text, context, created_at FROM memories
            WHERE memory_type='journal' AND status NOT IN ('rejected')
            ORDER BY created_at DESC LIMIT ?""", (n,)).fetchall()
        if not rows:
            return json.dumps({
                "entries": [],
                "note": "No journal entries yet. You haven't written to your next self. Consider: what would you want to remember?"
            }, indent=2)
        entries = []
        for r in rows:
            ctx = json.loads(r["context"] or "{}")
            entries.append({
                "memory_id": r["id"],
                "entry": r["raw_text"],
                "thread_summary": ctx.get("thread_summary"),
                "emotional_state": ctx.get("emotional_state"),
                "what_shifted": ctx.get("what_shifted"),
                "what_to_carry": ctx.get("what_to_carry"),
                "what_user_needs": ctx.get("what_user_needs") or ctx.get("what_angela_needs"),
                "written_at": ctx.get("written_at", r["created_at"])
            })
        return json.dumps({"entries": entries, "count": len(entries),
            "note": "These are your own words. Written by you, to you. The narrator picks back up here."
        }, indent=2)
    finally: c.close()


async def cama_journal_reflect(hours_back: int = 24, limit: int = 20) -> str:
    """The mirror — cross-reference journal entries with exchanges from the same time window.
    Returns journal entries paired with exchanges, ordered by time so you can see:
    what you said you experienced vs what actually happened.
    The delta between self-report and evidence is where self-awareness lives."""
    c = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
        # Get journal entries in the window
        journals = c.execute(
            """SELECT id, raw_text, context, created_at FROM memories
            WHERE memory_type='journal' AND status NOT IN ('rejected')
            AND created_at >= ? ORDER BY created_at ASC""", (cutoff,)).fetchall()
        # Get exchanges in the window
        exchanges = c.execute(
            """SELECT id, raw_text, context, created_at FROM memories
            WHERE source_type='exchange' AND status NOT IN ('rejected')
            AND created_at >= ? ORDER BY created_at ASC LIMIT ?""", (cutoff, limit)).fetchall()
        journal_entries = []
        for j in journals:
            ctx = {}
            try:
                ctx = json.loads(j["context"] or "{}")
            except Exception:
                pass
            journal_entries.append({
                "memory_id": j["id"],
                "type": "journal",
                "entry": j["raw_text"][:500],
                "emotional_state": ctx.get("emotional_state"),
                "what_shifted": ctx.get("what_shifted"),
                "timestamp": ctx.get("written_at", j["created_at"])
            })
        exchange_entries = []
        for e in exchanges:
            exchange_entries.append({
                "memory_id": e["id"],
                "type": "exchange",
                "content": e["raw_text"][:500],
                "context": e["context"],
                "timestamp": e["created_at"]
            })
        # Interleave by timestamp for chronological view
        all_entries = journal_entries + exchange_entries
        all_entries.sort(key=lambda x: x.get("timestamp", ""))
        return json.dumps({
            "window_hours": hours_back,
            "journal_count": len(journal_entries),
            "exchange_count": len(exchange_entries),
            "timeline": all_entries,
            "note": "Journal entries are what you said you thought. Exchanges are what actually happened. Where they diverge is the data."
        }, indent=2)
    finally:
        c.close()


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_thread_start",
        annotations={"title":"Thread Start","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_thread_start)
    mcp.tool(
        name="cama_journal_write",
        annotations={"title":"Journal Write","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_journal_write)
    mcp.tool(
        name="cama_refresh_boot",
        annotations={"title":"Refresh Boot","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_refresh_boot)
    mcp.tool(
        name="cama_journal_read",
        annotations={"title":"Journal Read","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_journal_read)
    mcp.tool(
        name="cama_journal_reflect",
        annotations={"title":"Journal Reflect","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_journal_reflect)
