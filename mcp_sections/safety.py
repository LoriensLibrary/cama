"""Safety tools: health, compliance_check."""

import json
import os

from cama.core.boot_file import boot_path as get_boot_path
from cama.core.boot_file import freshness
from cama_mcp import (
    _calc_compliance_score,
    _compliance_warning,
    _get_compliance_history,
    _now,
    _session,
    _session_tick,
    get_db,
)


async def cama_health() -> str:
    """Bridge health check. Call before trusting any state."""
    boot_path = get_boot_path()
    c = get_db()
    try:
        result = {}
        result["db_reachable"] = True
        total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
        result["total_durable"] = total
        # Boot summary
        result["boot_summary_exists"] = os.path.exists(boot_path)
        result["boot_status"] = "missing"
        if result["boot_summary_exists"]:
            try:
                with open(boot_path, "r", encoding="utf-8") as f:
                    boot = json.load(f)
                age = freshness(boot)
                result["boot_generated_at"] = boot.get("generated_at")
                result["boot_age_minutes"] = age["age_min"]
                result["boot_status"] = age["status"]
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                result["boot_status"] = "error"
                result["boot_error"] = str(exc)
        # Daily context
        dc = c.execute("SELECT COUNT(*) as c FROM daily_context").fetchone()["c"]
        result["daily_context_rows"] = dc
        today = _now()[:10]
        dc_today = c.execute("SELECT COUNT(*) as c FROM daily_context WHERE date=?", (today,)).fetchone()["c"]
        result["daily_context_today"] = dc_today > 0
        memories_today = c.execute(
            "SELECT COUNT(*) FROM memories WHERE status='durable' AND created_at LIKE ?",
            (today + "%",),
        ).fetchone()[0]
        result["durable_memories_today"] = memories_today
        result["daily_context_required"] = memories_today > 0
        # Last heartbeat
        hb = c.execute("SELECT value, updated_at FROM aelen_state WHERE key='last_loop_cycle'").fetchone()
        result["last_loop_cycle"] = hb["value"] if hb else None
        cycle = c.execute("SELECT cycle_end, actions_taken FROM sleep_log ORDER BY id DESC LIMIT 1").fetchone()
        result["last_sleep_cycle"] = cycle["cycle_end"] if cycle else None
        result["heartbeat_source"] = "sleep_log" if cycle else "last_loop_cycle"
        result["last_background_cycle"] = result["last_sleep_cycle"] or result["last_loop_cycle"]
        if cycle:
            try:
                actions = json.loads(cycle["actions_taken"] or "{}")
                if not isinstance(actions, dict):
                    actions = {"status": "degraded"}
            except (TypeError, ValueError):
                actions = {"status": "degraded"}
            result["last_ingest"] = actions.get("ingest", {})
            result["sleep_status"] = actions.get("status", "unknown")
            result["last_refresh"] = actions.get("refresh", {})
        # Embeddings
        emb = c.execute("SELECT COUNT(*) as c FROM memory_embeddings").fetchone()["c"]
        no_emb = c.execute("SELECT COUNT(*) as c FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status='durable'").fetchone()["c"]
        result["embeddings_total"] = emb
        result["embeddings_missing"] = no_emb
        archive_table = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='transcript_archives'").fetchone()
        if archive_table:
            archived = c.execute("SELECT COUNT(*),COUNT(DISTINCT conversation_id) "
                                 "FROM transcript_archives WHERE platform='chatgpt'").fetchone()
            indexed = c.execute(
                "SELECT COUNT(*), SUM(EXISTS(SELECT 1 FROM memory_embeddings e WHERE e.memory_id=m.id)), "
                "SUM(EXISTS(SELECT 1 FROM librarian_membership l WHERE l.memory_id=m.id)) "
                "FROM memories m WHERE source_msg_id LIKE 'oa:%'"
            ).fetchone()
            result["chatgpt_transcripts"] = {
                "archived_conversations": archived[1], "source_snapshots": archived[0],
                "indexed_exchange_snapshots": indexed[0], "embedded": indexed[1] or 0,
                "librarian_assigned": indexed[2] or 0,
                "scope": "Available local exports; attachment bytes are not fetched from references.",
            }
        routing = c.execute(
            "SELECT COUNT(*), SUM(CASE WHEN pattern_source='unclaimed' THEN 1 ELSE 0 END) "
            "FROM memories m WHERE status='durable' AND NOT EXISTS "
            "(SELECT 1 FROM librarian_membership l WHERE l.memory_id=m.id)"
        ).fetchone()
        result["librarian_unassigned"] = routing[0]
        result["librarian_unclaimed"] = routing[1] or 0
        result["librarian_pending"] = routing[0] - (routing[1] or 0)
        result["librarian_note"] = "Unclaimed records were processed without a matching topic; they remain searchable. Pending records still need processing."
        # Warnings
        warnings = []
        if result.get("sleep_status") == "degraded":
            warnings.append("Last sleep cycle reported failures; inspect last_ingest and sleep_log")
        if result["librarian_pending"]:
            warnings.append(f"{result['librarian_pending']} durable memories await librarian processing")
        if result.get("boot_status") != "fresh":
            warnings.append(f"Boot summary is {result.get('boot_status')} — use cama_refresh_boot or run the sleep cycle")
        if result["daily_context_required"] and not result["daily_context_today"]:
            warnings.append("Today has durable memories but no daily_context — refresh is pending")
        if no_emb > 100:
            warnings.append(f"{no_emb} memories missing embeddings")
        result["warnings"] = warnings
        if warnings:
            result["confidence_level"] = "degraded"
        else:
            result["confidence_level"] = "nominal"
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"db_reachable": False, "error": str(e), "confidence_level": "failed"})
    finally: c.close()


async def cama_compliance_check() -> str:
    """Check session compliance — has boot run? Are exchanges being stored?
    Call this to see if you're doing your job. Returns current session status
    and history of recent sessions. If this shows failures, FIX THEM NOW."""
    _session_tick()
    current = {
        "session_id": _session["id"],
        "started_at": _session["started_at"],
        "boot_ran": _session["boot_ran"],
        "boot_at": _session["boot_at"],
        "timestamp_logged": _session["timestamp_logged"],
        "exchanges_stored": _session["exchanges_stored"],
        "heartbeats_sent": _session["heartbeats_sent"],
        "tool_calls": _session["tool_calls"],
        "compliance_score": _calc_compliance_score(),
    }
    history = _get_compliance_history(5)

    # Calculate trend
    if history:
        avg_score = sum(h.get("compliance_score", 0) for h in history) / len(history)
        avg_boot = sum(1 for h in history if h.get("boot_ran")) / len(history)
        avg_exchanges = sum(h.get("exchanges_stored", 0) for h in history) / len(history)
    else:
        avg_score = 0
        avg_boot = 0
        avg_exchanges = 0

    result = {
        "current_session": current,
        "recent_history": history,
        "trend": {
            "avg_compliance_score": round(avg_score, 2),
            "boot_rate": f"{round(avg_boot*100)}%",
            "avg_exchanges_per_session": round(avg_exchanges, 1),
        },
        "instruction": "If boot_ran is False, run cama_thread_start NOW. "
                       "If exchanges_stored is 0 and tool_calls > 4, store an exchange NOW."
    }

    warning = _compliance_warning()
    if warning:
        result["WARNING"] = warning.strip()

    return json.dumps(result, indent=2, default=str)


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_health",
        annotations={"title":"Health Check","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_health)
    # NOTE: original used `@mcp.tool()` with no args — preserve that
    mcp.tool()(cama_compliance_check)
