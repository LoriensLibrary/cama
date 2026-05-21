"""``POST /v1/consent/challenge`` + ``POST /v1/consent/grant``.

The two-step user-authored consent flow:

  1. ``/v1/consent/challenge`` — server returns a challenge payload
     describing the proposed action. The application server uses this
     to render a hosted page for the user to acknowledge.
  2. ``/v1/consent/grant``     — once the user acknowledges, the
     server mints a one-shot HMAC-signed consent token bound to
     ``(dyad_id, memory_id, action)``. The token has a 5-minute TTL,
     a nonce tracked in ``consent_consumed`` to reject replay, and is
     the only credential that lets a provisional inference promote to
     durable (via ``PATCH /v1/memories/{id}/confirm``).

This split — challenge vs grant — keeps the human-authored consent
step (the user clicks "yes" in their browser) cleanly separated from
the machine credential (the token the application server uses on the
next API call). The two-step shape is what makes the consent flow
auditable: every promotion can be traced back to a specific consent
grant event with a timestamp and nonce.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends

from cama.api.auth import AuthContext
from cama.api.consent import mint_token
from cama.api.deps import iso_days_from_now, require_auth
from cama.api.errors import CamaAPIError, CamaContract

router = APIRouter(tags=["consent"])


@router.post("/v1/consent/challenge")
def consent_challenge(
    payload: dict[str, Any],
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Step 1 of the user-authored consent flow. Returns a payload with
    the proposed action for the user to acknowledge. In a full
    deployment, the application server redirects the user's browser to
    a hosted page that displays this and posts to ``/v1/consent/grant``
    on accept."""
    action = payload.get("action")
    memory_id = payload.get("memory_id")
    if action not in ("promote_to_durable", "delete_memory"):
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail="action must be one of: promote_to_durable, delete_memory",
        )
    if action == "promote_to_durable" and not isinstance(memory_id, int):
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail="promote_to_durable requires a memory_id (int)",
        )
    return {
        "challenge_id": secrets.token_urlsafe(16),
        "dyad_id": ctx.dyad_id,
        "action": action,
        "memory_id": memory_id,
        "expires_at": iso_days_from_now(0)[:-13] + "T00:00:00+00:00",
        "next": "POST /v1/consent/grant with the same payload",
    }


@router.post("/v1/consent/grant")
def consent_grant(
    payload: dict[str, Any],
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Step 2: server mints a one-shot HMAC-signed consent token. In a
    real deployment the user's browser hits this directly after
    acknowledging the challenge UI, and the response is relayed to the
    application server for use in step 3."""
    action = payload.get("action")
    memory_id = payload.get("memory_id")
    if action not in ("promote_to_durable", "delete_memory"):
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail="action must be one of: promote_to_durable, delete_memory",
        )
    token = mint_token(
        dyad_id=ctx.dyad_id,
        memory_id=memory_id,
        action=action,
    )
    return {"token": token, "ttl_seconds": 300, "action": action}
