"""``POST /v1/thread/start`` — warm boot.

v1 thread/start returns a dyad summary + most-recent memories. Full
warm-boot semantics (journal + blended retrieval + corrections) lives
on the MCP surface in ``cama_mcp.py:cama_thread_start``; porting that
into the API layer is named work in API.md § 13.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from cama.api.auth import AuthContext
from cama.api.deps import open_memory_db, require_auth
from cama.api.schemas import ThreadStartRequest, ThreadStartResponse

router = APIRouter(tags=["threads"])


@router.post("/v1/thread/start", response_model=ThreadStartResponse)
def thread_start(
    payload: ThreadStartRequest,
    ctx: AuthContext = Depends(require_auth),
) -> ThreadStartResponse:
    t0 = time.perf_counter()
    c = open_memory_db()
    try:
        total = c.execute(
            "SELECT COUNT(*) FROM memories WHERE status='durable' "
            "AND dyad_id = ?",
            (ctx.dyad_id,),
        ).fetchone()[0]
        recent = c.execute(
            "SELECT id, raw_text, memory_type, created_at "
            "FROM memories WHERE status='durable' AND dyad_id = ? "
            "ORDER BY created_at DESC LIMIT 5",
            (ctx.dyad_id,),
        ).fetchall()
    finally:
        c.close()

    resonant = [
        {
            "id": r["id"],
            "text": (r["raw_text"] or "")[:200],
            "type": r["memory_type"],
            "created_at": r["created_at"],
        }
        for r in recent
    ]

    return ThreadStartResponse(
        boot_status="refreshed",
        boot_age_min=0,
        journal_excerpt=(
            "v1 thread/start returns dyad summary + most-recent memories. "
            "Full warm-boot (journal + blended retrieval + corrections) "
            "is a follow-up enhancement that ports cama_mcp's "
            "cama_thread_start logic into the API layer."
        ),
        resonant_memories=resonant,
        corrections=[],
        compliance={"total_durable": total},
        performance_ms=round((time.perf_counter() - t0) * 1000.0, 2),
    )
