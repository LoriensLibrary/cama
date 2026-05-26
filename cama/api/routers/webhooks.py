"""Webhook subscription CRUD.

Endpoints:

  POST   /v1/webhooks         (mint a subscription; returns one-shot secret)
  GET    /v1/webhooks         (list; secret is NEVER in list responses)
  DELETE /v1/webhooks/{id}    (X-Confirm: <id> required)

The mint endpoint is the only place a webhook secret is ever surfaced
on the wire. It's hashed at rest (SHA-256) so the operator stores it
in their secrets manager on receipt, there is no recovery path. The
delivery mechanism itself lives in ``cama/api/webhooks.py``; this
router only manages subscriptions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse

from cama.api.auth import AuthContext
from cama.api.deps import require_auth
from cama.api.errors import CamaAPIError, CamaContract
from cama.api.webhooks import (
    KNOWN_EVENTS,
    create_webhook,
    delete_webhook,
    list_webhooks,
)

router = APIRouter(tags=["webhooks"])


@router.post("/v1/webhooks", status_code=status.HTTP_201_CREATED)
def webhook_create(
    payload: dict[str, Any],
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    url = payload.get("url")
    events = payload.get("events") or []
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail="`url` must be an http(s) URL",
        )
    if not isinstance(events, list) or not events:
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail="`events` must be a non-empty list of event types.",
        )
    unknown = [e for e in events if e not in KNOWN_EVENTS]
    if unknown:
        raise CamaAPIError(
            422,
            CamaContract.ENUM_VALUE_UNKNOWN,
            detail=(
                f"unknown event types: {unknown}. "
                f"valid: {list(KNOWN_EVENTS)}"
            ),
        )
    webhook_id, secret = create_webhook(
        dyad_id=ctx.dyad_id, url=url, events=events
    )
    return {
        "id": webhook_id,
        "dyad_id": ctx.dyad_id,
        "url": url,
        "events": events,
        "secret": secret,
        "note": "The 'secret' field is shown ONCE. Store it now.",
    }


@router.get("/v1/webhooks")
def webhook_list(
    ctx: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    items = list_webhooks(ctx.dyad_id)
    return {"webhooks": items, "count": len(items)}


@router.delete("/v1/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def webhook_delete(
    webhook_id: int,
    x_confirm: str | None = Header(default=None, alias="X-Confirm"),
    ctx: AuthContext = Depends(require_auth),
):
    if x_confirm is None or x_confirm != str(webhook_id):
        raise CamaAPIError(400, CamaContract.CONFIRM_HEADER_MISSING)
    if not delete_webhook(ctx.dyad_id, webhook_id):
        raise CamaAPIError(404, CamaContract.DYAD_SCOPE)
    return JSONResponse(status_code=204, content=None)
