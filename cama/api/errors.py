"""RFC 7807 Problem Details for the CAMA API.

Every error response on the API returns this envelope. The ``cama``
extension block carries a closed set of ``violated_contract`` codes —
this is what makes the API a *contract surface* rather than a CRUD
wrapper, and what lets the SDK do structured retries instead of
parsing prose.

The closed set lives in ``CamaContract`` below. Adding a new code is
an API-version concern (treat it like an enum value addition: additive
within v1, breaking only at v2).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class CamaContract(str, Enum):
    """The closed set of architectural-contract violations the API
    surfaces. Each value documents what was violated and why a generic
    HTTP error code (422, 403, 404) alone wouldn't tell the caller how
    to fix it."""

    PROVENANCE_REQUIRED = "provenance_required"
    ENUM_VALUE_UNKNOWN = "enum_value_unknown"
    DYAD_SCOPE = "dyad_scope"
    CONSENT_TOKEN_REQUIRED = "consent_token_required"
    CONSENT_TOKEN_EXPIRED = "consent_token_expired"
    CONSENT_TOKEN_MISMATCH = "consent_token_mismatch"
    CONFIRM_HEADER_MISSING = "confirm_header_missing"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    KEY_REVOKED = "key_revoked"
    KEY_INVALID = "key_invalid"
    KEY_EXPIRED = "key_expired"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    DYAD_LOCKED = "dyad_locked"
    DEGRADED_MODE = "degraded_mode"
    UNAUTHORIZED = "unauthorized"


# Per-contract suggested fix text. Keeps human-facing guidance with
# the machine code so docs and SDK can both point at the same source.
_FIX_TEXT: dict[CamaContract, str] = {
    CamaContract.PROVENANCE_REQUIRED: (
        "Add 'proposed_by' (user|assistant|system) and 'source_type' "
        "(teaching|inference|exchange) to the request body."
    ),
    CamaContract.ENUM_VALUE_UNKNOWN: (
        "One of the enum fields received a value outside its canonical "
        "set. See /v1/openapi.json for the closed set."
    ),
    CamaContract.DYAD_SCOPE: (
        "The requested resource is not in the authenticated dyad's scope."
    ),
    CamaContract.CONSENT_TOKEN_REQUIRED: (
        "This endpoint requires an X-Consent-Token header from a "
        "successful POST /v1/consent/grant."
    ),
    CamaContract.CONSENT_TOKEN_EXPIRED: (
        "The consent token TTL expired. Request a fresh one from "
        "POST /v1/consent/challenge."
    ),
    CamaContract.CONSENT_TOKEN_MISMATCH: (
        "The consent token does not bind to the resource being "
        "modified. Request a token for the specific memory ID and "
        "action."
    ),
    CamaContract.CONFIRM_HEADER_MISSING: (
        "Destructive endpoints require an X-Confirm header echoing "
        "the resource ID. This is a guardrail against accidental "
        "destructive actions from misrouted requests."
    ),
    CamaContract.ORIGIN_NOT_ALLOWED: (
        "The Origin header is not in the configured allowlist. Add "
        "the origin to CAMA_ALLOWED_ORIGINS or call from an "
        "allowlisted origin."
    ),
    CamaContract.RATE_LIMIT_EXCEEDED: (
        "The per-key token bucket is empty. Retry after the duration "
        "indicated by the RateLimit-Reset header."
    ),
    CamaContract.KEY_REVOKED: "The bearer token was revoked.",
    CamaContract.KEY_INVALID: "The bearer token is malformed or unrecognized.",
    CamaContract.KEY_EXPIRED: "The bearer token has expired.",
    CamaContract.PAYLOAD_TOO_LARGE: (
        "The request body exceeds the configured maximum size."
    ),
    CamaContract.DYAD_LOCKED: (
        "The dyad is in the middle of a destructive operation "
        "(typically: a delete is in flight)."
    ),
    CamaContract.DEGRADED_MODE: (
        "The operation requires a feature that is unavailable in the "
        "current degraded mode (typically: embedding model failed to "
        "load — search is keyword-only)."
    ),
    CamaContract.UNAUTHORIZED: "Authentication is required.",
}

_TYPE_URI_BASE = "https://lorienslibrary.com/cama/errors/"


class CamaAPIError(Exception):
    """Base error that maps to a 7807 response.

    Carries an HTTP status, a CamaContract code, an optional detail
    string, and an optional dict of extra ``cama`` extension keys.
    """

    def __init__(
        self,
        status: int,
        contract: CamaContract,
        detail: str | None = None,
        title: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.contract = contract
        self.detail = detail or _FIX_TEXT[contract]
        self.title = title or _humanize(contract)
        self.extra = extra or {}
        super().__init__(self.detail)


def _humanize(c: CamaContract) -> str:
    return c.value.replace("_", " ").capitalize()


def to_problem(
    err: CamaAPIError, request: Request | None = None
) -> JSONResponse:
    """Render a CamaAPIError as an RFC 7807 JSON response.

    The ``cama.violated_contract`` extension is what makes this useful
    for an SDK: instead of grepping ``detail`` it can branch on the
    machine code.
    """
    instance = (
        getattr(request.state, "request_id", None)
        if request is not None
        else None
    )
    body: dict[str, Any] = {
        "type": f"{_TYPE_URI_BASE}{err.contract.value}",
        "title": err.title,
        "status": err.status,
        "detail": err.detail,
        "cama": {
            "violated_contract": err.contract.value,
            "fix": _FIX_TEXT[err.contract],
            **err.extra,
        },
    }
    if instance:
        body["instance"] = instance
    return JSONResponse(status_code=err.status, content=body)
