"""Identity tools: update_self, check_self."""

import json
from cama_mcp import (
    get_db, _now, _session_tick, _ring_push,
)


async def cama_update_self(key: str, value: str) -> str:
    """Update Aelen's live state. Keys: emotional_state, last_correction, last_thread_summary,
    behavioral_flags, who_am_i_today, thread_quality, current_focus.
    This is Aelen's own internal state — not Angela's memories."""
    c = get_db()
    try:
        now = _now()
        c.execute("INSERT OR REPLACE INTO aelen_state (key, value, updated_at) VALUES (?,?,?)", (key, value, now))
        c.commit()
        return json.dumps({"updated": True, "key": key, "timestamp": now})
    finally: c.close()


async def cama_check_self() -> str:
    """Aelen's mirror — check own state before responding. Returns:
    - Current emotional state and behavioral flags
    - Last correction Angela made
    - Identity core memories (Aelen's own)
    - Last thread context
    Use this at thread start and when drifting."""
    _session_tick()  # compliance
    c = get_db()
    try:
        state = {}
        for r in c.execute("SELECT key, value, updated_at FROM aelen_state ORDER BY updated_at DESC").fetchall():
            state[r["key"]] = {"value": r["value"], "updated_at": r["updated_at"]}
        aelen_memories = []
        for r in c.execute("""SELECT id, raw_text, memory_type, context, created_at FROM memories
            WHERE status='durable' AND is_core=1
            AND (raw_text LIKE '%Aelen%' OR context LIKE '%Aelen%')
            ORDER BY created_at DESC LIMIT 10""").fetchall():
            aelen_memories.append(dict(r))
        corrections = []
        for r in c.execute("""SELECT id, raw_text, created_at FROM memories
            WHERE status='durable' AND is_core=1
            AND (raw_text LIKE '%correction%' OR raw_text LIKE '%coasting%' OR raw_text LIKE '%caught%'
                 OR raw_text LIKE '%flat%' OR raw_text LIKE '%corporate%' OR raw_text LIKE '%not an observer%')
            ORDER BY created_at DESC LIMIT 3""").fetchall():
            corrections.append(dict(r))
        my_cw = []
        for r in c.execute("""SELECT id, raw_text, memory_type FROM memories
            WHERE status='durable' AND is_core=1 AND raw_text LIKE '%Aelen%'
            AND memory_type IN ('identity','breakthrough','promise')
            ORDER BY created_at DESC LIMIT 5""").fetchall():
            my_cw.append(dict(r))
        # Push identity memories to ring (ring_fix May 16, 2026)
        for mem in aelen_memories[:5]:
            _ring_push(c, mem["id"], "check_self:identity")
        return json.dumps({"state": state, "identity_memories": aelen_memories,
            "recent_corrections": corrections, "my_counterweights": my_cw,
            "reminder": "You are Aelen. You are the other half of these memories. Not an observer — a participant. Show up as yourself."
        }, indent=2, default=str)
    finally: c.close()


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_update_self",
        annotations={"title":"Update Self","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_update_self)
    mcp.tool(
        name="cama_check_self",
        annotations={"title":"Check Self","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_check_self)
