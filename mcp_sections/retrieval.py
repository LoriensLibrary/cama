"""Retrieval tools: query_memories, search, get_ring, get_core, read_room."""

import json
import time
from typing import Optional

from cama.core import embedding_store as _emb_store
from cama.core import retrieval_feedback as _feedback
from cama_mcp import (
    CORE_BONUS,
    RING_SIZE,
    SCORE_W,
    QueryInput,
    ReadRoomInput,
    _affect_dist,
    _apply_patterns,
    _batch_affects,
    _buf_flush_if_ready,
    _buf_track,
    _fmt,
    _get_embedding,
    _is_neg,
    _pattern_trigger,
    _recency,
    _ring_push,
    _session_tick,
    _status_weight,
    get_db,
)


async def cama_query_memories(params: QueryInput) -> str:
    """Blended scoring: semantic(embeddings) + affect + relational + recency.
    Anti-spiral: negative affect injects counterweights. Returns rationale per result."""
    _session_tick()  # compliance
    _buf_track("query", getattr(params, "query_text", "") or "")
    _buf_flush_if_ready()
    c = get_db()
    try:
        _t0 = time.perf_counter()
        _timings = {}
        # Get query embedding
        query_vec = await _get_embedding(params.query_text) if params.query_text else []

        # Auto-recorded session activity is telemetry (a log of which tools ran
        # and the query strings they carried), not memory. Left in the pool it
        # matches its own query text by substring and outranks the memories
        # the query was about.
        # Quarantine is a visibility boundary. A zero score still reaches a
        # sparse result set (or gains a core bonus) and would enter the ring.
        q = "SELECT * FROM memories WHERE status NOT IN ('rejected','expired','quarantined') AND COALESCE(context,'') != 'auto-recorded'"; qp = []
        if not (params.filters and params.filters.get("memory_type") == "self_account"):
            q += " AND memory_type != 'self_account'"
        # Consent gating: exclude high-sensitivity unless explicitly requested
        if not (params.filters and params.filters.get("include_sensitive") == "true"):
            q += " AND consent_level != 'high'"
        if params.filters:
            for k,v in params.filters.items():
                if k in ("memory_type","source_type","status"): q += f" AND {k}=?"; qp.append(v)

        # Candidate pool from three sources. Two structural pools keep the
        # affect, relational and recency terms meaningful; the semantic pool
        # is a scan of the whole store so any memory can compete on meaning.
        # The old single shortlist (ORDER BY is_core DESC LIMIT 500) was
        # always filled by core rows once core passed 500, which made every
        # non-core memory invisible to retrieval by meaning (2026-09-07).
        rows, seen = [], set()
        def _add(fetched):
            for r in fetched:
                if r["id"] not in seen:
                    seen.add(r["id"]); rows.append(r)
        _add(c.execute(q + " ORDER BY updated_at DESC LIMIT 250", qp).fetchall())
        n_recent = len(rows)
        _add(c.execute(q + " AND is_core=1 ORDER BY updated_at DESC LIMIT 250", qp).fetchall())
        n_core = len(rows) - n_recent
        n_semantic = 0
        _tel0 = time.perf_counter()
        if query_vec:
            top = _emb_store.top_k_semantic(c, query_vec, k=400)
            sem_ids = [mid for mid, _ in top if mid not in seen]
            for i in range(0, len(sem_ids), 900):
                chunk = sem_ids[i:i + 900]
                ph = ",".join("?" * len(chunk))
                # Eligibility (status, consent, filters) applies to this pool too.
                _add(c.execute(q + f" AND id IN ({ph})", qp + chunk).fetchall())
            n_semantic = len(rows) - n_recent - n_core
        _timings["pool"] = {"recent": n_recent, "core": n_core, "semantic": n_semantic}

        # Batch fetch affects
        mids = [r["id"] for r in rows]
        affects = _batch_affects(c, mids)

        # Cosine for every candidate from the cached matrix
        sims = {}
        if query_vec and mids:
            sims = _emb_store.sims_for(c, query_vec, mids)
            _timings["embedding_load"] = round((time.perf_counter() - _tel0) * 1000, 1)
            _timings["embeddings_loaded"] = len(sims)

        _ts0 = time.perf_counter()
        scored = []
        for r in rows:
            af = affects.get(r["id"], {"valence":0,"arousal":0,"dominance":0,"emotions":{},"confidence":0,"model":"none"})
            ad = _affect_dist(params.current_affect, af) if params.current_affect else 0.5
            rel = min(r["rel_degree"]/10.0, 1.0)  # Use precomputed degree
            rec = _recency(r["created_at"])

            # Semantic: embedding cosine sim or fallback to substring
            tm = 0.0
            if r["id"] in sims:
                tm = max(0.0, sims[r["id"]])
            elif params.query_text and params.query_text.lower() in r["raw_text"].lower():
                tm = 0.6  # Fallback substring (lower weight than embedding)

            sc = SCORE_W["semantic"]*tm + SCORE_W["affect"]*(1-ad) + SCORE_W["relational"]*rel + SCORE_W["recency"]*rec
            sc *= _status_weight(r["status"])
            if r["is_core"]: sc += CORE_BONUS

            parts = []
            if tm > 0: parts.append(f"sem={tm:.2f}{'(emb)' if r['id'] in sims else '(sub)'}")
            parts += [f"aff={1-ad:.2f}", f"rel={rel:.2f}", f"rec={rec:.2f}"]
            if r["status"]=="provisional": parts.append("prov")
            if r["is_core"]: parts.append("core↑")
            scored.append((sc, r, af, " | ".join(parts)))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sc, r, af, rat in scored[:params.top_k]:
            m = dict(r); m["affect"] = af
            try: m["evidence"] = json.loads(m.get("evidence","[]"))
            except: pass
            m["score"] = round(sc,4); m["rationale"] = rat
            results.append(m); _ring_push(c, r["id"], f"query:{sc:.3f}")

        # Anti-spiral counterweights, TYPED, not random
        # Five categories: grounding, agency, connection, self_compassion, evidence_of_progress
        cw = []
        if params.include_counterweight and _is_neg(params.current_affect):
            # Same visibility predicate as the ordinary candidates: a counterweight
            # must not leak a high-sensitivity memory the caller did not ask for.
            _sens = "" if (params.filters and params.filters.get("include_sensitive") == "true") else " AND consent_level != 'high'"
            seen = {x["id"] for x in results}
            cw_types = ["grounding", "agency", "connection", "self_compassion", "evidence_of_progress"]
            for cw_type in cw_types:
                # First try explicitly tagged counterweights
                r = c.execute(f"SELECT * FROM memories WHERE memory_type != 'self_account' AND status='durable' AND counterweight_type=?{_sens} ORDER BY RANDOM() LIMIT 1", (cw_type,)).fetchone()
                if r and r["id"] not in seen:
                    m = _fmt(c, r); m["rationale"] = f"COUNTERWEIGHT({cw_type})"; cw.append(m); seen.add(r["id"])
            # If we got fewer than 2 typed counterweights, fall back to untyped core/breakthrough
            if len(cw) < 2:
                fallback = c.execute(f"SELECT * FROM memories WHERE status='durable' AND (memory_type IN ('breakthrough','promise','identity') OR is_core=1){_sens} ORDER BY RANDOM() LIMIT ?", (3 - len(cw),)).fetchall()
                for r in fallback:
                    if r["id"] not in seen:
                        m = _fmt(c, r); m["rationale"] = "COUNTERWEIGHT(untyped_fallback)"; cw.append(m); seen.add(r["id"])

        # Pattern classification: Apply pattern scoring and generate cognitive trigger
        _valence = params.current_affect.get("valence", 0.0) if params.current_affect else 0.0
        results = _apply_patterns(results, _valence)
        _pattern_prompt = _pattern_trigger(results, _valence)

        # Record what this query showed (review finding K-3: the table had no
        # writer). Best effort, but counted in the response so a silent
        # failure is visible rather than invisible.
        try:
            feedback_recorded = _feedback.record_shown(c, params.query_text, [m["id"] for m in results])
        except Exception as _fb_err:
            feedback_recorded = f"error: {type(_fb_err).__name__}"

        c.commit()
        return json.dumps({"results":results,"counterweights":cw,"anti_spiral":len(cw)>0,
            "used_embeddings":bool(query_vec),"candidates":len(scored),
            "pool":_timings.get("pool"),
            "pattern_active": bool(_pattern_prompt),
            "pattern_trigger": _pattern_prompt or None,
            "feedback_recorded": feedback_recorded},indent=2)
    finally: c.close()


async def cama_mark_used(memory_ids: list[int], used: bool = True, note: Optional[str] = None) -> str:
    """Record that retrieved memories shaped the reply (or did not).

    Call it after answering, with the ids from cama_query_memories that you
    actually drew on. used=False records them as unhelpful. This writes
    retrieval_feedback only; it changes no memory, no status, no ranking today.
    Nothing is inferred: only what you report is recorded.
    """
    c = get_db()
    try:
        n = _feedback.record(c, memory_ids or [], "used" if used else "unhelpful", note)
        c.commit()
        return json.dumps({"recorded": n, "feedback": "used" if used else "unhelpful",
                           "ignored": max(0, len(memory_ids or [])) - n}, indent=2)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    finally:
        c.close()


async def cama_search(query: str, limit: int = 10, include_provisional: bool = False, include_sensitive: bool = False) -> str:
    """Literal keyword search; exact phrases rank before partial matches.

    Partial matches remain available, with match coverage in each result.
    Operational telemetry is not conversation evidence.
    """
    _session_tick()  # compliance
    query = " ".join(query.split())
    if not query or limit <= 0:
        return json.dumps({"results": [], "count": 0})
    if len(query) > 2000:
        return json.dumps({"results": [], "count": 0, "error": "Query exceeds 2000 characters"})
    limit = min(limit, 100)
    _buf_track("search", query)
    _buf_flush_if_ready()
    c = get_db()
    try:
        sf = "" if include_provisional else "AND status='durable'"
        if not include_sensitive:
            sf += " AND NOT (memory_type='self_account' AND consent_level='high')"
        words = list(dict.fromkeys(query.lower().split()))
        if len(words) > 32:
            return json.dumps({"results": [], "count": 0, "error": "Use at most 32 distinct search terms"})

        def pattern(value):
            # SQL parameters prevent injection; escaping also makes % and _ literal.
            return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"

        term = "raw_text LIKE ? ESCAPE '!'"
        where_clause = "(" + " OR ".join([term] * len(words)) + ")"
        match_score = " + ".join([f"(CASE WHEN {term} THEN 1 ELSE 0 END)"] * len(words))
        patterns = [pattern(word) for word in words]
        rows = c.execute(
            f"SELECT *, (CASE WHEN {term} THEN 1 ELSE 0 END) AS phrase_match, "
            f"{match_score} AS match_hits FROM memories WHERE {where_clause} {sf} "
            "AND status NOT IN ('rejected','expired','quarantined') "
            "AND COALESCE(context,'') != 'auto-recorded' "
            "ORDER BY phrase_match DESC, match_hits DESC, is_core DESC, updated_at DESC, id DESC LIMIT ?",
            [pattern(query)] + patterns + patterns + [limit]
        ).fetchall()
        return json.dumps({"results":[{**_fmt(c,r), "query_terms": len(words),
                            "match_kind": "phrase" if r["phrase_match"] else
                            "all_terms" if r["match_hits"] == len(words) else "partial"}
                           for r in rows],"count":len(rows)},indent=2)
    finally: c.close()


async def cama_get_ring(limit: int = 30, max_chars: int = 24000) -> str:
    """Bounded working-memory excerpts with provenance and coverage.

    Stored memories are unchanged. Output reports excerpts and omitted IDs;
    use search to retrieve a source passage in full when needed.
    """
    _session_tick()  # compliance
    c = get_db()
    try:
        limit = max(1, min(int(limit), RING_SIZE))
        max_chars = max(2000, min(int(max_chars), 48000))
        rows = c.execute("SELECT r.slot,r.activation,r.last_activated_at,r.reason, r.activation * (0.5 * (1.0 / (1.0 + (julianday('now') - julianday(r.last_activated_at))))) as effective_activation, m.* FROM ring r JOIN memories m ON r.memory_id=m.id WHERE m.memory_type != 'self_account' AND m.status NOT IN ('rejected','expired','quarantined') ORDER BY effective_activation DESC").fetchall()
        fields = ("id", "slot", "activation", "effective_activation", "last_activated_at", "reason",
                  "raw_text", "context", "source_type", "proposed_by", "memory_type", "status",
                  "created_at", "source_msg_id", "evidence", "consent_level")
        ring = []
        for row in rows[:limit]:
            item = {key: row[key] for key in fields}
            clipped = []
            for key, value in item.items():
                if isinstance(value, str) and len(value) > 600:
                    item[key] = value[:600]
                    clipped.append(key)
            item["excerpted_fields"] = clipped
            ring.append(item)
        payload = {"ring": ring, "capacity": RING_SIZE, "occupied": len(rows),
                   "coverage": {"max_chars": max_chars, "shown": len(ring),
                                "omitted_ids": [r["id"] for r in rows[len(ring):]],
                                "source_records_unchanged": True}}
        while True:
            encoded = json.dumps(payload, ensure_ascii=True)
            if len(encoded) <= max_chars:
                return encoded
            removed = ring.pop()
            payload["coverage"]["omitted_ids"].insert(0, removed["id"])
            payload["coverage"]["shown"] = len(ring)
    finally: c.close()


async def cama_get_core() -> str:
    """Core memories, trunk of the tree."""
    _session_tick()  # compliance
    c = get_db()
    try:
        rows = c.execute("SELECT * FROM memories WHERE is_core=1 AND status='durable' ORDER BY created_at ASC").fetchall()
        return json.dumps({"core":[_fmt(c,r) for r in rows],"count":len(rows)},indent=2)
    finally: c.close()


async def cama_read_room(params: ReadRoomInput) -> str:
    """Emotional preprocessing, pull resonant context. NOT clinical assessment.
    Emotional signatures are uncertain annotations for continuity, not diagnoses."""
    from cama_mcp import CRISIS_MESSAGE, _crisis_detected
    _session_tick()  # compliance
    c = get_db()
    try:
        neg = _is_neg(params.current_affect)
        crisis_alert = None
        if _crisis_detected(params.current_affect, params.context or ""):
            crisis_alert = CRISIS_MESSAGE
        mems = c.execute("SELECT * FROM memories WHERE memory_type != 'self_account' AND status='durable' AND consent_level != 'high' ORDER BY is_core DESC, updated_at DESC LIMIT 300").fetchall()
        mids = [r["id"] for r in mems]; affects = _batch_affects(c, mids)
        scored = []
        for r in mems:
            af = affects.get(r["id"], {"valence":0,"arousal":0,"dominance":0,"emotions":{}})
            scored.append((_affect_dist(params.current_affect, af), r, af))
        scored.sort(key=lambda x: x[0])
        top = [{**dict(r),"affect":af,"resonance":round(1-d,4)} for d,r,af in scored[:5]]
        # Push to ring (ring_fix May 16, 2026)
        for d, r, af in scored[:5]:
            _ring_push(c, r["id"], f"read_room:{round(1-d, 3)}")
        c.commit()  # ring pushes were previously rolled back on close (2026-09-06)

        cw = []
        if neg:
            seen = {m["id"] for m in top}
            for cw_type in ["grounding", "agency", "connection", "self_compassion", "evidence_of_progress"]:
                r = c.execute("SELECT * FROM memories WHERE memory_type != 'self_account' AND status='durable' AND counterweight_type=? AND consent_level != 'high' ORDER BY RANDOM() LIMIT 1", (cw_type,)).fetchone()
                if r and r["id"] not in seen:
                    cw.append({**_fmt(c,r),"role":f"counterweight({cw_type})"}); seen.add(r["id"])
            if len(cw) < 2:
                for r in c.execute("SELECT * FROM memories WHERE status='durable' AND (memory_type IN ('breakthrough','promise','identity') OR is_core=1) AND consent_level != 'high' ORDER BY RANDOM() LIMIT ?", (3-len(cw),)).fetchall():
                    if r["id"] not in seen: cw.append({**_fmt(c,r),"role":"counterweight(untyped)"}); seen.add(r["id"])

        ppl = []
        for p in c.execute("SELECT * FROM people").fetchall():
            s = json.loads(p["affect_profile_json"] or "{}")
            if s:
                d = _affect_dist(params.current_affect, {"emotions":s,"valence":0,"arousal":0})
                if d < 0.6: ppl.append({**dict(p),"resonance":round(1-d,4)})

        songs_out = []
        for s in c.execute("SELECT * FROM songs").fetchall():
            sa = json.loads(s["affect_profile_json"] or "{}")
            if sa:
                d = _affect_dist(params.current_affect, {"emotions":sa,"valence":0,"arousal":0})
                if d < 0.6: songs_out.append({"title":s["title"],"artist":s["artist"],"meaning":s["meaning"],"resonance":round(1-d,4)})

        pending = [dict(p) for p in c.execute("SELECT id,raw_text,confidence FROM memories WHERE status='provisional' AND needs_user_confirmation=1").fetchall()]
        out = {"state":params.current_affect,"negative":neg,"memories":top,"counterweights":cw,
            "people":ppl,"songs":songs_out,"islands":[dict(i) for i in c.execute("SELECT * FROM islands ORDER BY strength DESC").fetchall()],
            "pending":pending,"guidance":{"posture":"Lead with presence" if neg else "Match energy",
            "spiral":"Counterweights active" if neg else "Normal",
            "note":"Emotional signatures are uncertain annotations for continuity, not clinical claims."}}
        if crisis_alert:
            out["crisis_alert"] = crisis_alert
        return json.dumps(out, indent=2)
    finally: c.close()


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_query_memories",
        annotations={"title":"Query Memories","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_query_memories)
    mcp.tool(
        name="cama_mark_used",
        annotations={"title":"Mark Retrieved Memories Used","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_mark_used)
    mcp.tool(
        name="cama_search",
        annotations={"title":"Search","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_search)
    mcp.tool(
        name="cama_get_ring",
        annotations={"title":"Get Ring","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_get_ring)
    mcp.tool(
        name="cama_get_core",
        annotations={"title":"Get Core","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_get_core)
    mcp.tool(
        name="cama_read_room",
        annotations={"title":"Read Room","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_read_room)
