"""Memory CRUD + provisional-to-durable promotion.

This router owns:

  POST   /v1/memories            (provenance enforced; source_type=inference
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
  2. Inferences cannot self-promote; source_type=inference is
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
from cama.api.consent import require_approver, verify_token
from cama.api.deps import (
    iso_days_from_now,
    open_memory_db,
    require_auth,
    row_to_memory,
)
from cama.api.errors import CamaAPIError, CamaContract
from cama.api.schemas import MemoryCreateRequest, MemoryResponse
from cama.api.webhooks import notify
from cama.core import cama_trust
from cama.core.time_utils import now_iso

router = APIRouter(tags=["memories"])


@router.post(
    "/v1/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    payload: MemoryCreateRequest,
    x_consent_approval: str | None = Header(default=None, alias="X-Consent-Approval"),
    ctx: AuthContext = Depends(require_auth),
) -> MemoryResponse:
    # Provenance is required at the schema level (Pydantic), so we
    # know payload.proposed_by and .source_type are set. The next
    # architectural check: inferences cannot self-promote to durable.
    # Force status = provisional for source_type=inference.
    # Caller-supplied provenance is not proof of human authority. Reuse the
    # separate consent-UI credential; ordinary application keys cannot claim
    # user authorship or write authoritative identity/core material.
    authoritative = (
        payload.source_type == "teaching"
        or payload.memory_type in ("teaching", "teaching_moment", "identity")
        or payload.is_core
    )
    if payload.proposed_by == "user" or authoritative:
        require_approver(x_consent_approval)
    if payload.source_type == "teaching" and payload.proposed_by != "user":
        raise CamaAPIError(
            422, CamaContract.PROVENANCE_REQUIRED,
            detail="Teachings require user authorship; use source_type=inference for hypotheses.",
        )
    if payload.source_type == "inference":
        forced_status = "provisional"
        # 30-day TTL for provisional inferences
        review_after = iso_days_from_now(30)
    else:
        forced_status = "durable"
        review_after = None
    # The same write-time trust decision the MCP store path makes
    # (mcp_sections.memory_lifecycle via cama_trust): text shaped like an
    # instruction to the AI is written quarantined, never durable or
    # provisional, and the decision is recorded in the guard log. The HTTP
    # surface used to skip this entirely.
    trust = cama_trust.classify(payload.source_type, payload.proposed_by, payload.text)
    if trust["quarantined"]:
        # status_override is honored universally, including the classifier's
        # fail-closed answer for a provenance pair it has no lane for (today:
        # proposed_by=system with source_type=inference or journal). A trusted
        # lane for those, if ever wanted, is a central policy in cama_trust,
        # not an API-only bypass.
        forced_status = "quarantined"

    # created_at and updated_at are both NOT NULL in the canonical schema
    # (cama_mcp._init). One timestamp for both so a freshly created memory
    # reads as never-modified.
    created = now_iso()
    c = open_memory_db()
    try:
        cur = c.execute(
            """
            INSERT INTO memories
                (raw_text, memory_type, context, source_type, status,
                 proposed_by, consent_level, review_after, is_core,
                 evidence, created_at, updated_at, dyad_id,
                 trust_score, trust_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                created,
                created,
                ctx.dyad_id,
                trust["trust_score"],
                trust["reason"],
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
    """Promote a provisional inference to durable, or release a quarantined
    memory to durable.

    Requires a one-shot HMAC-signed consent token bound to
    ``(dyad_id, memory_id, action="promote_to_durable")``. The token
    flow lives in ``routers/consent.py``; this endpoint is the only
    way a provisional row's status changes to ``"durable"``.

    Only ``provisional`` and ``quarantined`` rows can be confirmed. A
    rejected or expired row stays as it is (409
    ``status_not_confirmable``); rejection is kept for audit and expiry
    is resolved by TTL, and neither is undone by a consent token.
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
        was_quarantined = row["status"] == "quarantined"
        # Guarded UPDATE: the status check and the write are one statement,
        # so a row that changes state between the read and the write is not
        # promoted either.
        # Same reset as mcp_sections.memory_lifecycle.cama_confirm_memory:
        # the person vouched for the row, so it leaves review with full trust.
        cur = c.execute(
            "UPDATE memories SET status = 'durable', "
            "needs_user_confirmation = 0, confidence = 1.0, "
            "trust_score = 1.0, trust_reason = 'user_released', "
            "review_after = NULL, updated_at = ? "
            "WHERE id = ? AND dyad_id = ? "
            "AND status IN ('provisional', 'quarantined')",
            (now_iso(), memory_id, ctx.dyad_id),
        )
        if cur.rowcount != 1:
            c.rollback()
            raise CamaAPIError(
                409, CamaContract.STATUS_NOT_CONFIRMABLE,
                detail=f"memory {memory_id} has status {row['status']!r}; "
                       "only provisional or quarantined rows can be confirmed",
            )
        c.commit()
        row = c.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    finally:
        c.close()
    if was_quarantined:
        cama_trust._audit("memory_quarantine_released", "info",
                          {"memory_id": memory_id, "via": "api", "dyad_id": ctx.dyad_id})
    return row_to_memory(row, ctx.dyad_id)
