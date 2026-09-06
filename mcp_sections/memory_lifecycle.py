"""Memory Lifecycle tools: store_teaching, store_inference, store_exchange,
confirm_memory, reject_memory, delete_memory, expire_stale."""

import json
from typing import Optional

from cama.core import cama_trust
from cama_mcp import (
    StoreExchangeInput,
    StoreInferenceInput,
    StoreTeachingInput,
    _buf_reset,
    _compliance_tracker,
    _now,
    _ring_push,
    _session_mark_exchange,
    _store_affect,
    _store_embedding,
    get_db,
)


async def cama_store_teaching(params: StoreTeachingInput) -> str:
    """Store a TEACHING, user-authored durable memory. Authoritative, full weight, no expiry.
    Teachings are the user's truth. They define identity and relationship."""
    c = get_db()
    try:
        now = _now(); ev = [{"quote":params.evidence_quote,"timestamp":now}] if params.evidence_quote else []
        tr = cama_trust.classify("teaching", "user", params.raw_text)
        status = tr["status_override"] or "durable"
        cur = c.execute("INSERT INTO memories (raw_text,memory_type,context,source_type,status,proposed_by,evidence,confidence,consent_level,counterweight_type,is_core,trust_score,trust_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (params.raw_text, params.memory_type, params.context, "teaching", status, "user", json.dumps(ev), 1.0, params.consent_level, params.counterweight_type, 1 if params.is_core else 0, tr["trust_score"], tr["reason"], now, now))
        mid = cur.lastrowid
        _store_affect(c, mid, params.emotions, params.valence, params.arousal, conf=0.9)
        if params.island_name:
            isl = c.execute("SELECT island_id FROM islands WHERE name=?", (params.island_name,)).fetchone()
            if isl: c.execute("INSERT OR IGNORE INTO island_members (island_id,memory_id,strength) VALUES (?,?,?)", (isl["island_id"], mid, max(params.emotions.values()) if params.emotions else 0.5))
        c.commit()  # SHELF WRITE COMMITTED, durable regardless of ring
        ring_ok = True
        if tr["quarantined"]:
            ring_ok = False  # Quarantined memories never enter the active ring
        else:
            try:
                _ring_push(c, mid, "new_teaching")
                c.commit()
            except Exception:
                c.rollback(); ring_ok = False  # Ring failed but shelves are safe
        await _store_embedding(c, mid, params.raw_text)
        c.commit()  # Commit embedding separately
        # Auto-tag: route this teaching to matching librarians (April 29, 2026)
        _tag_result = None
        try:
            from cama.librarian import cama_auto_tag
            _tag_result = cama_auto_tag.tag_memory(c, mid, params.raw_text, params.context)
            c.commit()
        except Exception as _tag_err:
            print(f"[CAMA] Auto-tag failed for teaching {mid}: {_tag_err}", file=__import__('sys').stderr)
        from cama_mcp import EMBEDDING_API_KEY
        return json.dumps({"stored":True,"memory_id":mid,"source_type":"teaching","status":status,"is_core":params.is_core,
            "has_embedding":bool(EMBEDDING_API_KEY),"ring_ok":ring_ok,
            "quarantined":tr["quarantined"],"trust_score":tr["trust_score"],"trust_reason":tr["reason"],
            "auto_tagged_to": (_tag_result or {}).get("tagged_to", []),
            "rationale": ("QUARANTINED — stored and searchable, but kept out of boot/ring until released via cama_confirm_memory. Reason: "+tr["reason"]) if tr["quarantined"] else ("Teaching → durable, full weight." + (" (ring write failed but shelf is safe)" if not ring_ok else ""))},indent=2)
    finally: c.close()


async def cama_store_inference(params: StoreInferenceInput) -> str:
    """Store an INFERENCE, provisional hypothesis. Full weight, no expiry, confirmable to durable.
    Inferences are hypotheses that persist. Confirm promotes to durable. Reject zeroes them."""
    c = get_db()
    try:
        now = _now()
        ev = [{"quote":q,"timestamp":now} for q in params.evidence_quotes]
        tr = cama_trust.classify("inference", "assistant", params.raw_text)
        # An assistant inference is provisional until the user confirms it;
        # the trust classifier may downgrade that to quarantined, never upgrade
        # it to durable.
        status = tr["status_override"] or "provisional"
        cur = c.execute("INSERT INTO memories (raw_text,memory_type,context,source_type,status,proposed_by,evidence,confidence,review_after,needs_user_confirmation,is_core,trust_score,trust_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (params.raw_text, params.memory_type, params.context, "inference", status, "assistant", json.dumps(ev), params.confidence, None, 1, 0, tr["trust_score"], tr["reason"], now, now))
        mid = cur.lastrowid
        _store_affect(c, mid, params.emotions, params.valence, params.arousal, conf=params.confidence, model="inferred")
        c.commit()  # SHELF WRITE COMMITTED, durable regardless of ring
        # Ring push in its own transaction: the old code pushed after the last
        # commit and the write was rolled back on close (2026-09-06).
        ring_ok = True
        if tr["quarantined"]:
            ring_ok = False  # quarantined rows stay out of working memory until released
        else:
            try:
                _ring_push(c, mid, "new_inference")
                c.commit()
            except Exception:
                c.rollback(); ring_ok = False
        await _store_embedding(c, mid, params.raw_text)
        c.commit()  # Commit embedding separately
        # Auto-tag: route this inference to matching librarians (April 29, 2026)
        _tag_result = None
        try:
            from cama.librarian import cama_auto_tag
            _tag_result = cama_auto_tag.tag_memory(c, mid, params.raw_text, params.context)
            c.commit()
        except Exception as _tag_err:
            print(f"[CAMA] Auto-tag failed for inference {mid}: {_tag_err}", file=__import__('sys').stderr)
        # Report what was actually stored, not what we intended to store.
        row = c.execute("SELECT source_type, status, needs_user_confirmation FROM memories WHERE id=?", (mid,)).fetchone()
        return json.dumps({"stored":True,"memory_id":mid,"source_type":row["source_type"],"status":row["status"],
            "needs_user_confirmation":bool(row["needs_user_confirmation"]),
            "confidence":params.confidence,"expires":None,"ring_ok":ring_ok,
            "quarantined":tr["quarantined"],"trust_score":tr["trust_score"],"trust_reason":tr["reason"],
            "auto_tagged_to": (_tag_result or {}).get("tagged_to", []),
            "rationale": ("QUARANTINED, searchable but kept out of boot/ring until released via cama_confirm_memory. Reason: " + tr["reason"]) if tr["quarantined"]
                         else "Inference → provisional. Full weight, no expiry. Confirm to promote to durable."
                              + (" (ring write failed but shelf is safe)" if not ring_ok else "")},indent=2)
    finally: c.close()


async def cama_store_exchange(params: StoreExchangeInput) -> str:
    """Store a conversation EXCHANGE -- full user+assistant turn as one durable memory.
    Exchanges are facts -- what was actually said. Durable, full weight, no expiry.
    Emotionally tagged in real-time by the assistant. Used for conversation continuity."""
    if _compliance_tracker: _compliance_tracker.mark_exchange()
    _session_mark_exchange()  # inline tracker too
    _buf_reset()  # Manual store resets auto-record counter
    c = get_db()
    try:
        now = _now()
        # Combine both messages with clear markers
        raw_text = f"[USER] {params.user_message}\n[ASSISTANT] {params.assistant_message}"
        # Build context with thread_id if provided
        ctx = params.context or ""
        if params.thread_id:
            ctx = f"[thread:{params.thread_id}] {ctx}".strip()
        tr = cama_trust.classify("exchange", "system", raw_text)
        status = tr["status_override"] or "durable"
        cur = c.execute(
            "INSERT INTO memories (raw_text,memory_type,context,source_type,status,proposed_by,evidence,confidence,consent_level,is_core,trust_score,trust_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (raw_text, params.memory_type, ctx or None, "exchange", status, "system", "[]", 1.0, "low", 0, tr["trust_score"], tr["reason"], now, now)
        )
        mid = cur.lastrowid
        _store_affect(c, mid, params.emotions, params.valence, params.arousal, conf=0.8, model="realtime")
        c.commit()  # Shelf write committed
        # Ring push -- optional, don't fail the store if ring is full
        ring_ok = True
        if tr["quarantined"]:
            ring_ok = False  # Quarantined memories never enter the active ring
        else:
            try:
                _ring_push(c, mid, "exchange")
                c.commit()
            except Exception:
                c.rollback()
                ring_ok = False
        # Embedding for semantic retrieval
        await _store_embedding(c, mid, raw_text)
        c.commit()
        # Auto-tag: route this exchange to matching librarians (April 29, 2026)
        _tag_result = None
        try:
            from cama.librarian import cama_auto_tag
            _tag_result = cama_auto_tag.tag_memory(c, mid, raw_text, ctx)
            c.commit()
        except Exception as _tag_err:
            print(f"[CAMA] Auto-tag failed for exchange {mid}: {_tag_err}", file=__import__('sys').stderr)
        return json.dumps({
            "stored": True,
            "memory_id": mid,
            "source_type": "exchange",
            "status": status,
            "ring_ok": ring_ok,
            "quarantined": tr["quarantined"],
            "trust_score": tr["trust_score"],
            "trust_reason": tr["reason"],
            "chars_stored": len(raw_text),
            "auto_tagged_to": (_tag_result or {}).get("tagged_to", []),
            "rationale": ("QUARANTINED — stored and searchable, but excluded from boot/ring until released via cama_confirm_memory. Reason: "+tr["reason"]) if tr["quarantined"] else "Exchange stored -- durable, emotionally tagged, searchable."
        }, indent=2)
    finally:
        c.close()


async def cama_confirm_memory(memory_id: int) -> str:
    """Promote provisional → durable, or release a quarantined memory → durable.
    The memory handshake: the user vouches for it. Releasing a quarantined memory
    resets its trust to full and lets it enter boot/ring again."""
    c = get_db()
    try:
        m = c.execute("SELECT status FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not m: return json.dumps({"error":"Not found"})
        if m["status"] not in ("provisional", "quarantined"):
            return json.dumps({"error":f"Already {m['status']}"})
        was_quarantined = m["status"] == "quarantined"
        c.execute("UPDATE memories SET status='durable',needs_user_confirmation=0,confidence=1.0,trust_score=1.0,trust_reason='user_released',review_after=NULL,updated_at=? WHERE id=?", (_now(), memory_id))
        c.commit()
        if was_quarantined:
            try:
                _ring_push(c, memory_id, "quarantine_released"); c.commit()
            except Exception:
                c.rollback()
            cama_trust._audit("memory_quarantine_released", "info", {"memory_id": memory_id})
        return json.dumps({"promoted":True,"memory_id":memory_id,"new_status":"durable",
            "released_from_quarantine":was_quarantined},indent=2)
    finally: c.close()


async def cama_reject_memory(memory_id: int, reason: Optional[str] = None) -> str:
    """Reject, user contradicted. Kept for audit, zero retrieval weight."""
    c = get_db()
    try:
        m = c.execute("SELECT status FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not m: return json.dumps({"error":"Not found"})
        c.execute("UPDATE memories SET status='rejected',updated_at=?,context=COALESCE(context,'')||? WHERE id=?", (_now(), f" [REJECTED: {reason or 'contradicted'}]", memory_id))
        # A rejected memory has no business in working memory.
        evicted = c.execute("DELETE FROM ring WHERE memory_id=?", (memory_id,)).rowcount
        c.commit()
        return json.dumps({"rejected":True,"memory_id":memory_id,"previous_status":m["status"],"ring_evicted":bool(evicted)},indent=2)
    finally: c.close()


async def cama_delete_memory(memory_id: int) -> str:
    """Permanently delete a memory. Part of trust, easy delete."""
    c = get_db()
    try:
        c.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        c.commit()
        return json.dumps({"deleted":True,"memory_id":memory_id},indent=2)
    finally: c.close()


async def cama_expire_stale() -> str:
    """Expire provisionals past TTL. Status='expired' (softer than rejected, not confirmed ≠ contradicted)."""
    c = get_db()
    try:
        now = _now()
        exp = c.execute("SELECT id FROM memories WHERE status='provisional' AND review_after IS NOT NULL AND review_after<?", (now,)).fetchall()
        for r in exp:
            c.execute("UPDATE memories SET status='expired',updated_at=? WHERE id=?", (now, r["id"]))
            c.execute("DELETE FROM ring WHERE memory_id=?", (r["id"],))
        c.commit()
        return json.dumps({"expired":len(exp),"ids":[r["id"] for r in exp],"note":"expired ≠ rejected. Not confirmed in time, not contradicted."},indent=2)
    finally: c.close()


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_store_teaching",
        annotations={"title":"Store Teaching","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_store_teaching)
    mcp.tool(
        name="cama_store_inference",
        annotations={"title":"Store Inference","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_store_inference)
    mcp.tool(
        name="cama_store_exchange",
        annotations={"title":"Store Exchange","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_store_exchange)
    mcp.tool(
        name="cama_confirm_memory",
        annotations={"title":"Confirm Memory","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_confirm_memory)
    mcp.tool(
        name="cama_reject_memory",
        annotations={"title":"Reject Memory","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_reject_memory)
    mcp.tool(
        name="cama_delete_memory",
        annotations={"title":"Delete Memory","readOnlyHint":False,"destructiveHint":True,"idempotentHint":True,"openWorldHint":False},
    )(cama_delete_memory)
    mcp.tool(
        name="cama_expire_stale",
        annotations={"title":"Expire Stale","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_expire_stale)
