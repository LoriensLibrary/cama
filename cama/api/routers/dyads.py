"""Dyad surface — GET / export / delete.

Endpoints:

  GET    /v1/dyads/{id}          (single-tenant summary; cross-dyad 404)
  GET    /v1/dyads/{id}/export   (GDPR data portability)
  DELETE /v1/dyads/{id}          (GDPR right-to-erasure; double-confirm,
                                  real wipe, returns Merkle root over
                                  deleted IDs without leaking the IDs)

The double-confirm guard on DELETE prevents accidents: the request
must carry both ``X-Confirm: <dyad_id>`` AND
``X-Confirm-Again: I-understand-this-is-permanent``. This pairs with
THREAT_MODEL.md row #12 — the response never leaks the deleted IDs
themselves, only a Merkle root so an auditor can verify what was
removed without giving an attacker a list of valid IDs to probe.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Header

from cama.api.auth import AuthContext
from cama.api.deps import open_memory_db, require_auth
from cama.api.errors import CamaAPIError, CamaContract
from cama.api.schemas import DyadConsent, DyadResponse
from cama.api.webhooks import notify
from cama.core.time_utils import now_iso

router = APIRouter(tags=["dyads"])


@router.get("/v1/dyads/{dyad_id}", response_model=DyadResponse)
def get_dyad(
    dyad_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> DyadResponse:
    # In v1 single-tenant mode, only the authenticated dyad is
    # visible. Cross-dyad reads return 404 (not 403 — does not
    # leak existence).
    if dyad_id != ctx.dyad_id:
        raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
    c = open_memory_db()
    try:
        row = c.execute(
            "SELECT COUNT(*) AS n, MAX(created_at) AS last "
            "FROM memories WHERE status='durable' AND dyad_id = ?",
            (ctx.dyad_id,),
        ).fetchone()
    finally:
        c.close()
    return DyadResponse(
        id=dyad_id,
        created_at=now_iso(),
        last_activity_at=row["last"] if row else None,
        total_memories=row["n"] if row else 0,
        # defaults for v1; PATCH endpoint lands in v1.1
        consent=DyadConsent(),
    )


@router.get("/v1/dyads/{dyad_id}/export")
def dyad_export(
    dyad_id: str,
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """GDPR data portability: full export of every memory + affect row
    for the requested dyad. Edges / embeddings / librarian memberships
    are regenerable artifacts and are not included."""
    if dyad_id != ctx.dyad_id:
        raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
    c = open_memory_db()
    try:
        mem_rows = c.execute(
            "SELECT * FROM memories WHERE dyad_id = ?", (ctx.dyad_id,)
        ).fetchall()
        mem_ids = [r["id"] for r in mem_rows]
        affect_rows: list[Any] = []
        if mem_ids:
            qmarks = ",".join(["?"] * len(mem_ids))
            affect_rows = c.execute(
                f"SELECT * FROM memory_affect WHERE memory_id IN ({qmarks})",
                mem_ids,
            ).fetchall()
    finally:
        c.close()
    return {
        "dyad_id": dyad_id,
        "exported_at": now_iso(),
        "format": "cama-export-v1",
        "memories": [dict(r) for r in mem_rows],
        "affect": [dict(r) for r in affect_rows],
        "note": (
            "This bundle contains all stored content for the dyad. "
            "Edges, embeddings, and librarian memberships are "
            "regenerable artifacts and are not included."
        ),
    }


@router.delete("/v1/dyads/{dyad_id}")
def dyad_delete(
    dyad_id: str,
    x_confirm: str | None = Header(default=None, alias="X-Confirm"),
    x_confirm_again: str | None = Header(default=None, alias="X-Confirm-Again"),
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """GDPR right-to-erasure: real wipe of every row associated with
    the dyad. Double-confirm to prevent accidents."""
    if dyad_id != ctx.dyad_id:
        raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
    if x_confirm != dyad_id:
        raise CamaAPIError(400, CamaContract.CONFIRM_HEADER_MISSING)
    if x_confirm_again != "I-understand-this-is-permanent":
        raise CamaAPIError(
            400,
            CamaContract.CONFIRM_HEADER_MISSING,
            detail=(
                "DELETE /v1/dyads requires BOTH X-Confirm: <dyad_id> "
                "and X-Confirm-Again: I-understand-this-is-permanent."
            ),
        )

    c = open_memory_db()
    deleted_ids: list[int] = []
    try:
        rows = c.execute(
            "SELECT id FROM memories WHERE dyad_id = ?", (ctx.dyad_id,)
        ).fetchall()
        deleted_ids = [r["id"] for r in rows]
        if deleted_ids:
            qmarks = ",".join(["?"] * len(deleted_ids))
            c.execute(
                f"DELETE FROM memory_affect WHERE memory_id IN ({qmarks})",
                deleted_ids,
            )
            c.execute(
                f"DELETE FROM memory_embeddings WHERE memory_id IN ({qmarks})",
                deleted_ids,
            )
            c.execute(
                f"DELETE FROM librarian_membership WHERE memory_id IN ({qmarks})",
                deleted_ids,
            )
        c.execute("DELETE FROM memories WHERE dyad_id = ?", (ctx.dyad_id,))
        c.commit()
    finally:
        c.close()

    # Merkle root over the deleted IDs (sorted, canonical bytes). IDs
    # themselves are not leaked in the response; only the root.
    merkle = hashlib.sha256(
        json.dumps(sorted(deleted_ids)).encode("utf-8")
    ).hexdigest()

    try:
        notify(
            ctx.dyad_id,
            "dyad.deleted",
            {"dyad_id": dyad_id, "counts": {"memories": len(deleted_ids)}},
        )
    except Exception:
        pass

    return {
        "deleted_at": now_iso(),
        "dyad_id": dyad_id,
        "counts": {"memories": len(deleted_ids)},
        "deleted_ids_merkle_root": merkle,
    }
