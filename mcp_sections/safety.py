"""Safety tools: health, compliance_check."""

import json
import os
from cama_mcp import (
    get_db, _now, _session_tick, _session,
    _calc_compliance_score, _get_compliance_history, _compliance_warning,
)


async def cama_health() -> str:
    """Bridge health check. Call before trusting any state."""
    boot_path = os.path.expanduser("~/.cama/boot_summary.json")
    c = get_db()
    try:
        result = {}
        result["db_reachable"] = True
        total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
        result["total_durable"] = total
        # Boot summary
        if os.path.exists(boot_path):
            import json as j2
            with open(boot_path,"r") as f:
                boot = j2.load(f)
            gen = boot.get("generated_at","")
            result["boot_summary_exists"] = True
            result["boot_generated_at"] = gen
            if gen:
                try:
                    from datetime import datetime, timezone
                    gen_dt = datetime.fromisoformat(gen)
                    age = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 60
                    result["boot_age_minutes"] = round(age, 1)
                    result["boot_status"] = "fresh" if age < 60 else "stale" if age < 360 else "cold"
                except: result["boot_status"] = "unknown"
        else:
            result["boot_summary_exists"] = False
            result["boot_status"] = "missing"
        # Daily context
        dc = c.execute("SELECT COUNT(*) as c FROM daily_context").fetchone()["c"]
        result["daily_context_rows"] = dc
        today = _now()[:10]
        dc_today = c.execute("SELECT COUNT(*) as c FROM daily_context WHERE date=?", (today,)).fetchone()["c"]
        result["daily_context_today"] = dc_today > 0
        # Last heartbeat
        hb = c.execute("SELECT value, updated_at FROM aelen_state WHERE key='last_loop_cycle'").fetchone()
        result["last_loop_cycle"] = hb["value"] if hb else None
        # Embeddings
        emb = c.execute("SELECT COUNT(*) as c FROM memory_embeddings").fetchone()["c"]
        no_emb = c.execute("SELECT COUNT(*) as c FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status='durable'").fetchone()["c"]
        result["embeddings_total"] = emb
        result["embeddings_missing"] = no_emb
        # Warnings
        warnings = []
        if result.get("boot_status") in ("stale","cold","missing"):
            warnings.append(f"Boot summary is {result.get('boot_status')} � run cama_loop.py")
        if not result.get("daily_context_today"):
            warnings.append("No daily_context for today � loop may not have run")
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
