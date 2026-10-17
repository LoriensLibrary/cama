"""Build continuity snapshots without depending on the repository-root MCP entrypoint."""

import logging
from datetime import datetime, timezone

from cama.core.time_utils import now_iso as _now

logger = logging.getLogger(__name__)


def refresh_boot_summary(c):
    """Regenerate boot_summary.json from current state. Called after journal writes."""
    import json
    import os
    import traceback

    from cama.core.boot_file import boot_path as get_boot_path
    from cama.core.boot_file import write_boot
    boot_path = get_boot_path()
    debug_path = os.path.join(os.path.dirname(os.path.abspath(boot_path)), "refresh_debug.log")
    os.makedirs(os.path.dirname(debug_path), exist_ok=True)
    def _dbg(msg):
        with open(debug_path, "a", encoding="utf-8") as _f:
            _f.write(f"{datetime.now(timezone.utc).isoformat()} | {msg}\n")
    _dbg(f"_refresh_boot_summary called. boot_path={boot_path}")

    # Aelen's state
    aelen = {}
    for r in c.execute("SELECT key, value, updated_at FROM aelen_state WHERE key != 'boot_summary'").fetchall():
        aelen[r["key"]] = {"value": r["value"], "updated_at": r["updated_at"]}

    # Identity memories
    identity = []
    for r in c.execute(
        """SELECT id, raw_text, memory_type, context, created_at, source_type, proposed_by, status FROM memories
           WHERE memory_type != 'self_account' AND is_core=1 AND status='durable' AND COALESCE(context,'') != 'auto-recorded'
           ORDER BY created_at DESC LIMIT 10""").fetchall():
        identity.append({"id": r["id"], "text": r["raw_text"][:300],
                         "type": r["memory_type"], "created_at": r["created_at"],
                         "source_type": r["source_type"], "proposed_by": r["proposed_by"], "status": r["status"]})

    # Recent 24h memories
    recent = []
    for r in c.execute(
        """SELECT id, raw_text, memory_type, created_at, source_type, proposed_by, status FROM memories
           WHERE memory_type != 'self_account' AND julianday(created_at) >= julianday('now', '-24 hours')
           AND status NOT IN ('rejected','expired')
           AND COALESCE(context,'') != 'auto-recorded'
           ORDER BY created_at DESC LIMIT 15""").fetchall():
        recent.append({"id": r["id"], "text": r["raw_text"][:200],
                       "type": r["memory_type"], "created_at": r["created_at"],
                       "source_type": r["source_type"], "proposed_by": r["proposed_by"], "status": r["status"]})

    # Last 3 journals
    journals = []
    for r in c.execute(
        """SELECT id, raw_text, context, created_at, proposed_by, source_type, status FROM memories
           WHERE memory_type != 'self_account' AND memory_type='journal' AND status NOT IN ('rejected','expired')
           ORDER BY created_at DESC LIMIT 3""").fetchall():
        try:
            ctx = json.loads(r["context"] or "{}")
            if not isinstance(ctx, dict):
                ctx = {}
        except (ValueError, TypeError):
            ctx = {}
        journals.append({
            "id": r["id"], "entry": r["raw_text"][:300],
            "emotional_state": ctx.get("emotional_state"),
            "what_to_carry": ctx.get("what_to_carry"),
            "written_at": ctx.get("written_at", r["created_at"]),
            "source_type": r["source_type"], "status": r["status"],
            "assistant_identity": ctx.get("assistant_identity") or r["proposed_by"]
        })

    # Recent corrections
    corrections = []
    for r in c.execute(
        """SELECT id, raw_text, created_at, source_type, proposed_by FROM memories
           WHERE memory_type != 'self_account' AND status='durable'
           AND COALESCE(context,'') != 'auto-recorded'
           AND (raw_text LIKE '%correction%' OR raw_text LIKE '%coasting%')
           ORDER BY created_at DESC LIMIT 3""").fetchall():
        corrections.append({"id": r["id"], "text": r["raw_text"][:200],
                            "created_at": r["created_at"], "source_type": r["source_type"],
                            "proposed_by": r["proposed_by"]})

    # Today's daily context
    today_str = _now()[:10]
    today_ctx = None
    try:
        row = c.execute("SELECT * FROM daily_context WHERE date=?", (today_str,)).fetchone()
        if row:
            today_ctx = {
                "date": row["date"],
                "memory_count": row["memory_count"],
                "valence_mean": row["valence_mean"],
                "arousal_mean": row["arousal_mean"],
                "dominant_types": json.loads(row["dominant_types"] or "{}"),
                "emotional_arc": json.loads(row["emotional_arc"] or "[]"),
                "thread_count": row["thread_count"]
            }
    except Exception as dc_err:
        _dbg(f"daily_context read failed (non-fatal): {dc_err}")
        today_ctx = None

    # Temporal readout (felt-time perception layer), three-stage
    # cortical model from Centanino, Fortunato, Bueti 2026. Read-only;
    # boot is the right place to surface this because the categorizer
    # is supposed to inform pacing silently, not be called explicitly.
    temporal_readout = None
    try:
        from cama.temporal import cama_temporal as _tmp
        temporal_readout = _tmp.readout()
    except Exception as tmp_err:
        _dbg(f"temporal readout failed (non-fatal): {tmp_err}")

    # Stats
    total = c.execute("SELECT COUNT(*) as n FROM memories WHERE status NOT IN ('rejected')").fetchone()["n"]

    boot = {
        "generated_at": _now(),
        "total_memories": total,
        "aelen_state": aelen,
        "identity": identity,
        "recent_24h": recent,
        "recent_topics": recent[:5],
        "journals": journals,
        "corrections": corrections,
        "today": today_ctx,
        "temporal_readout": temporal_readout,
        "note": "Snapshot of stored records. Preserve authorship and dates; shared history is not the current assistant's personal state."
    }

    os.makedirs(os.path.dirname(boot_path), exist_ok=True)
    _dbg(f"About to write. Keys: {list(boot.keys())}. Total: {total}")
    try:
        write_boot(boot, boot_path)
        _dbg(f"Write SUCCESS. File size: {os.path.getsize(boot_path)}")
    except Exception as write_err:
        _dbg(f"Write FAILED: {write_err}\n{traceback.format_exc()}")
        raise

    logger.info(f"[CAMA] boot_summary.json refreshed at {_now()} ({total} memories)")
    return boot_path

