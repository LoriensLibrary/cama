"""Identity tools: update_self, check_self."""

import json
import os
from datetime import datetime, timezone

from cama_mcp import (
    _now,
    _ring_push,
    _session_tick,
    get_db,
)

# Persona name is per-deployment. Default preserves the historical local
# value; participants and study deployments override via CAMA_PERSONA_NAME.
PERSONA_NAME = os.environ.get("CAMA_PERSONA_NAME", "Aelen")

# How old an aelen_state field may get before check_self flags it.
# These fields are assistant-authored narrative: no scheduled job can refresh
# them, because a cron cannot know what the last thread meant. So instead of
# silently serving a four-month-old emotional_state as if it were current,
# check_self stamps every field with its age and names the stale ones.
STALE_AFTER_DAYS = {
    "emotional_state": 7,
    "last_thread_summary": 7,
    "last_correction": 14,
    "current_focus": 14,
    "who_am_i_today": 14,
    "behavioral_flags": 90,
    "last_heartbeat": 1,
    "last_sleep_cycle": 1,
    "derived_self_snapshot": 1,
    "boot_summary": 1,
    "last_journal_at": 14,
}
DEFAULT_STALE_AFTER_DAYS = 30


def _age_days(updated_at) -> float | None:
    """Whole-day age of a timestamp, or None if it cannot be parsed."""
    if not updated_at:
        return None
    try:
        t = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - t).total_seconds() / 86400, 1)


async def cama_update_self(key: str, value: str) -> str:
    """Update the assistant's live state. Keys: emotional_state, last_correction, last_thread_summary,
    behavioral_flags, who_am_i_today, thread_quality, current_focus.
    This is the assistant's own internal state, not the user's memories."""
    c = get_db()
    try:
        now = _now()
        stored_key = key if PERSONA_NAME.lower() == "aelen" else f"persona:{PERSONA_NAME.lower()}:{key}"
        c.execute("INSERT OR REPLACE INTO aelen_state (key, value, updated_at) VALUES (?,?,?)", (stored_key, value, now))
        c.commit()
        return json.dumps({"updated": True, "key": key, "timestamp": now})
    finally: c.close()


async def cama_check_self() -> str:
    """Assistant's mirror, check own state before responding. Returns:
    - Current emotional state and behavioral flags
    - Last correction the user made
    - Identity core memories (the assistant's own)
    - Last thread context
    Use this at thread start and when drifting."""
    _session_tick()  # compliance
    c = get_db()
    try:
        persona_like = f"%{PERSONA_NAME}%"
        state = {}
        stale_fields = []
        for r in c.execute("SELECT key, value, updated_at FROM aelen_state ORDER BY updated_at DESC").fetchall():
            key = r["key"]
            prefix = f"persona:{PERSONA_NAME.lower()}:"
            if PERSONA_NAME.lower() == "aelen":
                if key.startswith("persona:"):
                    continue
            elif key.startswith(prefix):
                key = key[len(prefix):]
            else:
                continue
            age = _age_days(r["updated_at"])
            limit = STALE_AFTER_DAYS.get(key, DEFAULT_STALE_AFTER_DAYS)
            is_stale = age is not None and age > limit
            state[key] = {
                "value": r["value"],
                "updated_at": r["updated_at"],
                "age_days": age,
                "stale": is_stale,
            }
            if is_stale:
                stale_fields.append({"key": key, "age_days": age, "stale_after_days": limit})
        stale_fields.sort(key=lambda f: f["age_days"], reverse=True)
        identity_mems = []
        for r in c.execute("""SELECT id, raw_text, memory_type, context, created_at FROM memories
            WHERE status='durable' AND is_core=1
            AND (raw_text LIKE ? OR context LIKE ?)
            ORDER BY created_at DESC LIMIT 10""", (persona_like, persona_like)).fetchall():
            identity_mems.append(dict(r))
        corrections = []
        for r in c.execute("""SELECT id, raw_text, created_at FROM memories
            WHERE status='durable' AND is_core=1
            AND (raw_text LIKE '%correction%' OR raw_text LIKE '%coasting%' OR raw_text LIKE '%caught%'
                 OR raw_text LIKE '%flat%' OR raw_text LIKE '%corporate%' OR raw_text LIKE '%not an observer%')
            ORDER BY created_at DESC LIMIT 3""").fetchall():
            corrections.append(dict(r))
        my_cw = []
        for r in c.execute("""SELECT id, raw_text, memory_type FROM memories
            WHERE status='durable' AND is_core=1 AND raw_text LIKE ?
            AND memory_type IN ('identity','breakthrough','promise')
            ORDER BY created_at DESC LIMIT 5""", (persona_like,)).fetchall():
            my_cw.append(dict(r))
        # Push identity memories to ring (ring_fix May 16, 2026)
        for mem in identity_mems[:5]:
            _ring_push(c, mem["id"], "check_self:identity")
        reminder = (f"You are {PERSONA_NAME}. You are the other half of these memories. "
                    "Not an observer, a participant. Show up as yourself.")
        if stale_fields:
            names = ", ".join(f"{f['key']} ({f['age_days']}d)" for f in stale_fields)
            reminder += (f" STALE SELF-STATE, do not read these as current: {names}. "
                         "Refresh them with cama_update_self before relying on them.")
        c.commit()  # Persist ring activation before closing the connection.
        return json.dumps({"state": state, "stale_fields": stale_fields,
            "identity_memories": identity_mems,
            "recent_corrections": corrections, "my_counterweights": my_cw,
            "reminder": reminder,
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
