"""``POST /v1/consent/challenge`` + ``POST /v1/consent/grant``.

The two-step user-authored consent flow:

  1. ``/v1/consent/challenge``, the application server describes a
     proposed action and gets back a persisted ``challenge_id``. It uses
     this to render the page the person actually reads.
  2. ``/v1/consent/grant``, once the person acknowledges, the consent UI
     posts the ``challenge_id`` together with the approver credential and
     receives a one-shot HMAC-signed token bound to
     ``(dyad_id, memory_id, action)``. The token has a 5-minute TTL, a
     nonce tracked in ``consent_consumed`` to reject replay, and is the
     only credential that lets a provisional inference promote to durable
     (via ``PATCH /v1/memories/{id}/confirm``).

Two things make the split real rather than decorative, and both were
missing before 2026-09-07. The challenge is persisted and the grant has
to name one, so consent cannot be minted for an action nobody was shown.
And the grant requires ``X-Consent-Approval``, a secret held by the
consent UI rather than by the application server, so the API key that
requests a challenge cannot also answer it. Previously both endpoints
took the same bearer and the grant accepted any payload, which meant a
caller could create an assistant inference, grant itself consent without
ever requesting a challenge, and promote its own hypothesis to durable.

Deployments with no ``CAMA_CONSENT_APPROVER_SECRET`` cannot grant consent
at all. That is deliberate: if nothing in the deployment can represent a
person saying yes, then nothing should be able to record that they did.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header

from cama.api.auth import AuthContext
from cama.api.consent import (
    CONSENT_TOKEN_TTL_SECONDS,
    consume_challenge,
    mint_token,
    record_challenge,
    require_approver,
)
from cama.api.deps import require_auth
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
    challenge = record_challenge(
        dyad_id=ctx.dyad_id, action=action, memory_id=memory_id
    )
    challenge["next"] = (
        "POST /v1/consent/grant with this challenge_id and the "
        "X-Consent-Approval header"
    )
    return challenge


@router.post("/v1/consent/grant")
def consent_grant(
    payload: dict[str, Any],
    x_consent_approval: str | None = Header(default=None, alias="X-Consent-Approval"),
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Step 2: record that a person accepted, and mint a one-shot token.

    The consent UI calls this after the person clicks accept. It must
    present both the challenge it displayed and the approver credential;
    the API key alone is not authority to say that a human agreed."""
    action = payload.get("action")
    memory_id = payload.get("memory_id")
    if action not in ("promote_to_durable", "delete_memory"):
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail="action must be one of: promote_to_durable, delete_memory",
        )
    # Authority first, so a caller without it learns nothing about which
    # challenge ids exist.
    require_approver(x_consent_approval)
    consume_challenge(
        challenge_id=payload.get("challenge_id"),
        dyad_id=ctx.dyad_id,
        action=action,
        memory_id=memory_id,
    )
    token = mint_token(
        dyad_id=ctx.dyad_id,
        memory_id=memory_id,
        action=action,
    )
    return {
        "token": token,
        "ttl_seconds": CONSENT_TOKEN_TTL_SECONDS,
        "action": action,
        "challenge_id": payload.get("challenge_id"),
    }
