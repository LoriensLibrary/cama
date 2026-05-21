"""Typed exceptions for the CAMA SDK.

The server returns RFC 7807 Problem Details with a ``cama.violated_contract``
extension code. The SDK maps each contract code to a Python exception
class so callers can ``except CamaProvenanceError`` rather than parse
``response.json()`` themselves.

The mapping is in ``error_for_contract`` at the bottom. New contracts
on the server side should land here too.
"""

from __future__ import annotations

from typing import Any


class CamaError(Exception):
    """Base class for every API-mapped error.

    Attributes:
        status: HTTP status code from the server
        contract: the ``cama.violated_contract`` code (e.g. ``provenance_required``)
        detail: human-readable detail message
        fix: server-supplied actionable fix
        instance: server request ID for support tickets
        body: the raw response body, kept for debugging
    """

    def __init__(
        self,
        status: int,
        contract: str,
        detail: str,
        fix: str | None = None,
        instance: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.contract = contract
        self.detail = detail
        self.fix = fix
        self.instance = instance
        self.body = body
        msg = f"[{contract}] {detail}"
        if fix:
            msg += f"  fix: {fix}"
        super().__init__(msg)


class CamaProvenanceError(CamaError):
    """``POST /v1/memories`` missing required provenance fields, or
    enum value outside the canonical set."""


class CamaEnumValueUnknownError(CamaError):
    """An enum field (memory_type, source_type, proposed_by) was set
    to a value outside the published closed set. Check
    ``/v1/openapi.json`` for the canonical enums."""


class CamaDyadScopeError(CamaError):
    """The requested resource is not in the authenticated dyad's scope.

    Per the API contract this returns 404 (not 403) so existence
    of out-of-scope resources is not leaked."""


class CamaConsentTokenError(CamaError):
    """Base class for the three consent-token failure modes."""


class CamaConsentTokenRequired(CamaConsentTokenError):
    """The endpoint requires X-Consent-Token but none was provided."""


class CamaConsentTokenExpired(CamaConsentTokenError):
    """The consent token's 5-minute TTL has expired. Request a fresh
    one from ``POST /v1/consent/challenge``."""


class CamaConsentTokenMismatch(CamaConsentTokenError):
    """The token does not bind to the (memory_id, action) pair being
    modified."""


class CamaKeyError(CamaError):
    """Bearer-token-related errors: invalid prefix, revoked, expired,
    or unauthorized."""


class CamaRateLimitError(CamaError):
    """The per-key token bucket is empty. Retry after
    ``response.headers['RateLimit-Reset']`` seconds."""


class CamaDegradedModeError(CamaError):
    """The server is in degraded mode (typically embedding model
    failed to load) and cannot service this operation. Check
    ``/v1/health``."""


class CamaDyadLockedError(CamaError):
    """The dyad has a destructive operation in flight (typically a
    delete) and cannot accept other writes until that completes."""


class CamaConfirmHeaderMissingError(CamaError):
    """Destructive endpoint called without ``X-Confirm``."""


class CamaPayloadTooLargeError(CamaError):
    """Request body exceeded the server's configured maximum."""


class CamaOriginNotAllowedError(CamaError):
    """Consent endpoint called from a non-allowlisted Origin."""


# ---------------------------------------------------------------------------
# Contract-code dispatch
# ---------------------------------------------------------------------------
_CONTRACT_TO_EXC: dict[str, type[CamaError]] = {
    "provenance_required": CamaProvenanceError,
    "enum_value_unknown": CamaEnumValueUnknownError,
    "dyad_scope": CamaDyadScopeError,
    "consent_token_required": CamaConsentTokenRequired,
    "consent_token_expired": CamaConsentTokenExpired,
    "consent_token_mismatch": CamaConsentTokenMismatch,
    "confirm_header_missing": CamaConfirmHeaderMissingError,
    "origin_not_allowed": CamaOriginNotAllowedError,
    "rate_limit_exceeded": CamaRateLimitError,
    "key_invalid": CamaKeyError,
    "key_revoked": CamaKeyError,
    "key_expired": CamaKeyError,
    "unauthorized": CamaKeyError,
    "payload_too_large": CamaPayloadTooLargeError,
    "dyad_locked": CamaDyadLockedError,
    "degraded_mode": CamaDegradedModeError,
}


def error_for_response(
    status: int, body: dict[str, Any] | None
) -> CamaError:
    """Build a typed exception from the raw response.

    Falls back to the base ``CamaError`` if the contract code is
    unknown — this gives the SDK forward-compatibility with new
    server-side contracts. Code should check ``CamaError.contract``
    in that case.
    """
    cama = (body or {}).get("cama", {})
    contract = cama.get("violated_contract") or "unknown"
    detail = (body or {}).get("detail") or f"HTTP {status}"
    fix = cama.get("fix")
    instance = (body or {}).get("instance")
    exc_cls = _CONTRACT_TO_EXC.get(contract, CamaError)
    return exc_cls(
        status=status,
        contract=contract,
        detail=detail,
        fix=fix,
        instance=instance,
        body=body,
    )
