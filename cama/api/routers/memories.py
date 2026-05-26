"""Memory CRUD + provisional-to-durable promotion.

This router owns:

  POST   /v1/memories            (provenance enforced; assistant+inference
                                  is forced to status=provisional)
  GET    /v1/memories/{id}       (dyad-scoped; cross-dyad reads 404)
  DELETE /v1/memories/{id}       (X-Confirm: <id> required)
  PATCH  /v1/memories/{id}/confirm
                                 (one-shot consent token required;
                                  promotes provisional inferences to
                                  durable, the only place that's
                                  allowed to happen)

The four architectural commitments enforced here (per API.md § 2):

  1. Provenance NOT NULL at the API boundary.
  2. AI cannot self-promote inferences, assistant+inference is
     forced to provisional with a 30-day TTL.
  3. Dyad scope leaks nothing, cross-dyad reads return 404 (not 403).
  4. Destructive endpoints require explicit X-Confirm match.

Webhook delivery (``memory.created``, ``memory.deleted``) is
best-effort: a failed delivery is logged to ``webhook_deliveries`` but
never raises out of the originating handler.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse

from cama.api.auth import AuthContext
from cama.api.consent import verify_token
from cama.api.deps import (
    iso_days_from_now,
    open_memory_db,
    require_auth,
    row_to_memory,
)
from cama.api.errors import CamaAPIError, CamaContract
from cama.api.schemas import MemoryCreateRequest, MemoryResponse
from cama.api.webhooks import notify
from cama.core.time_utils import now_iso

router = APIRouter(tags=["memories"])


@router.post(
    "/v1/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    payload: MemoryCreateRequest,
    ctx: AuthContext = Depends(require_auth),
) -> MemoryResponse:
    # Provenance is required at the schema level (Pydantic), so we
    # know payload.proposed_by and .source_type are set. The next
    # architectural check: inferences cannot self-promote to durable.
    # Force status = provisional for assistant+inference.
    if payload.proposed_by == "assistant" and payload.source_type == "inference":
        forced_status = "provisional"
        # 30-day TTL for provisional inferences
        review_after = iso_days_from_now(30)
    else:
        forced_status = "durable"
        review_after = None

    c = open_memory_db()
    try:
        cur = c.execute(
            """
            INSERT INTO memories
                (raw_text, memory_type, context, source_type, status,
                 proposed_by, consent_level, review_after, is_core,
                 evidence, created_at, updated_at, dyad_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.text,
                payload.memory_type,
                payload.context,
                payload.source_type,
                forced_status,
                payload.proposed_by,
                payload.consent_level,
                review_after,
                int(payload.is_core),
                payload.evidence,
                now_iso(),
                None,
                ctx.dyad_id,
            ),
        )
        memory_id = cur.lastrowid

        if payload.affect:
            c.execute(
                """
                INSERT INTO memory_affect
                    (memory_id, valence, arousal, dominance,
                     emotion_json, confidence, computed_at, model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    payload.affect.valence,
                    payload.affect.arousal,
                    payload.affect.dominance,
                    json.dumps(payload.affect.emotions),
                    payload.affect.confidence,
                    now_iso(),
                    "api_caller",
                ),
            )

        c.commit()
        row = c.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    finally:
        c.close()

    mem = row_to_memory(row, ctx.dyad_id)
    # Fire webhook subscriptions for memory.created (best-effort;
    # delivery failures are logged but never block the response).
    try:
        notify(
            ctx.dyad_id,
            "memory.created",
            {"id": mem.id, "memory_type": mem.memory_type, "status": mem.status},
        )
    except Exception:
        pass
    return mem


@router.get("/v1/memories/{memory_id}", response_model=MemoryResponse)
def get_memory(
    memory_id: int,
    ctx: AuthContext = Depends(require_auth),
) -> MemoryResponse:
    c = open_memory_db()
    try:
        row = c.execute(
            "SELECT * FROM memories WHERE id = ? AND dyad_id = ?",
            (memory_id, ctx.dyad_id),
        ).fetchone()
    finally:
        c.close()
    if row is None:
        # Return 404, not 403, to avoid leaking the existence of
        # IDs in other dyads. (THREAT_MODEL.md row #2)
        raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
    return row_to_memory(row, ctx.dyad_id)


@router.delete(
    "/v1/memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_memory(
    memory_id: int,
    x_confirm: str | None = Header(default=None, alias="X-Confirm"),
    ctx: AuthContext = Depends(require_auth),
):
    if x_confirm is None or x_confirm != str(memory_id):
        raise CamaAPIError(400, CamaContract.CONFIRM_HEADER_MISSING)
    c = open_memory_db()
    try:
        row = c.execute(
            "SELECT id FROM memories WHERE id = ? AND dyad_id = ?",
            (memory_id, ctx.dyad_id),
        ).fetchone()
        if row is None:
            raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
        c.execute("DELETE FROM memory_affect WHERE memory_id = ?", (memory_id,))
        c.execute(
            "DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,)
        )
        c.execute(
            "DELETE FROM librarian_membership WHERE memory_id = ?", (memory_id,)
        )
        c.execute(
            "DELETE FROM memories WHERE id = ? AND dyad_id = ?",
            (memory_id, ctx.dyad_id),
        )
        c.commit()
    finally:
        c.close()
    # Notify subscribers of memory.deleted
    try:
        notify(ctx.dyad_id, "memory.deleted", {"id": memory_id})
    except Exception:
        pass
    return JSONResponse(status_code=204, content=None)


@router.patch("/v1/memories/{memory_id}/confirm")
def memory_confirm(
    memory_id: int,
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    ctx: AuthContext = Depends(require_auth),
) -> MemoryResponse:
    """Promote a provisional inference to durable.

    Requires a one-shot HMAC-signed consent token bound to
    ``(dyad_id, memory_id, action="promote_to_durable")``. The token
    flow lives in ``routers/consent.py``; this endpoint is the only
    way a provisional row's status changes to ``"durable"``.
    """
    if not x_consent_token:
        raise CamaAPIError(401, CamaContract.CONSENT_TOKEN_REQUIRED)
    verify_token(
        x_consent_token,
        expected_dyad_id=ctx.dyad_id,
        expected_memory_id=memory_id,
        expected_action="promote_to_durable",
    )
    c = open_memory_db()
    try:
        row = c.execute(
            "SELECT * FROM memories WHERE id = ? AND dyad_id = ?",
            (memory_id, ctx.dyad_id),
        ).fetchone()
        if row is None:
            raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
        c.execute(
            "UPDATE memories SET status = 'durable', "
            "review_after = NULL, updated_at = ? "
            "WHERE id = ? AND dyad_id = ?",
            (now_iso(), memory_id, ctx.dyad_id),
        )
        c.commit()
        row = c.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    finally:
        c.close()
    return row_to_memory(row, ctx.dyad_id)
