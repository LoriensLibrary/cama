"""User-authored consent-token flow for inference promotion.

Architectural mechanism for the API.md commitment "AI cannot
self-promote teachings." A caller that wants to promote a
provisional assistant-inference memory to durable must obtain a
consent token via the two-step flow:

  1. POST /v1/consent/challenge, application server requests a
     challenge for (dyad_id, memory_id, action). Server returns a
     URL the end-user's browser visits.
  2. (user sees the proposed inference and clicks Accept)
     POST /v1/consent/grant, server validates the challenge,
     returns a one-shot HMAC-signed token.
  3. The application server attaches the token in
     PATCH /v1/memories/{id}/confirm to actually promote the row.

Token format: ``base64url(payload).base64url(signature)`` where
payload is the canonical JSON of
``{"dyad_id":..., "memory_id":..., "action":..., "nonce":..., "exp":...}``
and signature is HMAC-SHA256(payload) keyed on the operator's
``CAMA_CONSENT_SECRET`` env var.

For MVP the consent UI itself is out of scope, the operator hosts
the user-facing acceptance page. This module only handles
mint/verify.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from cama.api.auth import _open_keys_db
from cama.api.errors import CamaAPIError, CamaContract

CONSENT_TOKEN_TTL_SECONDS = 300  # 5 minutes per API.md
CONSENT_ACTIONS = ("promote_to_durable", "delete_memory", "delete_dyad")


def _secret() -> bytes:
    """Read the HMAC secret from env. In production this is set once
    at deploy time and rotated infrequently. For dev / test the
    fallback is a process-local random, fine because consent
    tokens are short-TTL anyway."""
    s = os.environ.get("CAMA_CONSENT_SECRET")
    if s:
        return s.encode("utf-8")
    # Per-process fallback: cache so the same process always uses the
    # same bytes (otherwise consent challenge issued at T0 can't be
    # validated at T1).
    global _DEV_FALLBACK_SECRET
    try:
        return _DEV_FALLBACK_SECRET
    except NameError:
        _DEV_FALLBACK_SECRET = secrets.token_bytes(32)
        return _DEV_FALLBACK_SECRET


CONSENT_CHALLENGE_TTL_SECONDS = 300  # a person has 5 minutes to accept


def init_consent_schema() -> None:
    """Tracks issued challenges and consumed nonces.

    ``consent_consumed`` makes a minted token one-shot. ``consent_challenges``
    makes the grant step provable: a grant has to name a challenge that was
    actually issued, for this dyad, this action and this memory, and has not
    already been used. Without the table the challenge step was decorative,
    the challenge_id was generated and thrown away and grant never asked for
    one, so a caller could mint consent for an action nobody was ever shown.
    """
    c = _open_keys_db()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS consent_consumed (
                nonce TEXT PRIMARY KEY,
                dyad_id TEXT NOT NULL,
                memory_id INTEGER,
                action TEXT NOT NULL,
                consumed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consent_challenges (
                challenge_id TEXT PRIMARY KEY,
                dyad_id TEXT NOT NULL,
                action TEXT NOT NULL,
                memory_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                granted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_challenge_dyad
                ON consent_challenges(dyad_id);
        """)
        c.commit()
    finally:
        c.close()


def approver_secret() -> str | None:
    """The credential that stands for a human acceptance.

    Held by the consent page the person actually interacts with, not by
    the application server. The application server holds an API key,
    which is why an API key alone must not be able to grant consent:
    otherwise the machine records the human's decision on the human's
    behalf, which is the one thing the two-step flow exists to prevent.
    """
    s = os.environ.get("CAMA_CONSENT_APPROVER_SECRET", "").strip()
    return s or None


def require_approver(presented: str | None) -> None:
    """Raise unless the caller presented the approver credential.

    Fails closed. A deployment with no approver secret configured cannot
    grant consent at all, because there is nothing in that deployment
    that can represent a person saying yes.
    """
    expected = approver_secret()
    if expected is None:
        raise CamaAPIError(503, CamaContract.CONSENT_APPROVER_NOT_CONFIGURED)
    if not presented:
        raise CamaAPIError(401, CamaContract.CONSENT_APPROVER_REQUIRED)
    if not hmac.compare_digest(presented, expected):
        raise CamaAPIError(403, CamaContract.CONSENT_APPROVER_REQUIRED,
                           detail="approver credential did not match")


def record_challenge(*, dyad_id: str, action: str, memory_id: int | None) -> dict:
    """Persist a challenge and return the row the caller should echo back."""
    init_consent_schema()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=CONSENT_CHALLENGE_TTL_SECONDS)
    challenge_id = secrets.token_urlsafe(16)
    c = _open_keys_db()
    try:
        c.execute(
            "INSERT INTO consent_challenges "
            "(challenge_id, dyad_id, action, memory_id, created_at, expires_at, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (challenge_id, dyad_id, action, memory_id,
             now.isoformat(), expires.isoformat()),
        )
        c.commit()
    finally:
        c.close()
    return {
        "challenge_id": challenge_id,
        "dyad_id": dyad_id,
        "action": action,
        "memory_id": memory_id,
        "expires_at": expires.isoformat(),
    }


def consume_challenge(
    *, challenge_id: str | None, dyad_id: str, action: str, memory_id: int | None
) -> dict:
    """Validate a challenge and mark it granted. Raises on any mismatch.

    Every check is a separate way the old code could be fooled: no
    challenge at all, someone else's challenge, a challenge for a
    different memory or a different action, a stale one, or one already
    spent.
    """
    if not challenge_id:
        raise CamaAPIError(422, CamaContract.CONSENT_CHALLENGE_REQUIRED)
    init_consent_schema()
    c = _open_keys_db()
    try:
        row = c.execute(
            "SELECT * FROM consent_challenges WHERE challenge_id = ?",
            (challenge_id,),
        ).fetchone()
        if row is None:
            raise CamaAPIError(403, CamaContract.CONSENT_CHALLENGE_INVALID,
                               detail="no such challenge")
        if row["granted_at"] is not None:
            raise CamaAPIError(403, CamaContract.CONSENT_CHALLENGE_INVALID,
                               detail="challenge already granted")
        if row["dyad_id"] != dyad_id:
            raise CamaAPIError(403, CamaContract.CONSENT_CHALLENGE_INVALID,
                               detail="challenge belongs to a different dyad")
        if row["action"] != action:
            raise CamaAPIError(403, CamaContract.CONSENT_CHALLENGE_INVALID,
                               detail="challenge describes a different action")
        if row["memory_id"] != memory_id:
            raise CamaAPIError(403, CamaContract.CONSENT_CHALLENGE_INVALID,
                               detail="challenge describes a different memory")
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            raise CamaAPIError(403, CamaContract.CONSENT_CHALLENGE_INVALID,
                               detail="challenge expired")
        c.execute(
            "UPDATE consent_challenges SET granted_at = ? WHERE challenge_id = ?",
            (datetime.now(timezone.utc).isoformat(), challenge_id),
        )
        c.commit()
        return dict(row)
    finally:
        c.close()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def mint_token(
    *, dyad_id: str, memory_id: int | None, action: str
) -> str:
    """Mint a one-shot consent token. The caller stores the nonce so
    a later ``verify_token`` can reject replays."""
    if action not in CONSENT_ACTIONS:
        raise ValueError(f"unknown consent action {action!r}")
    exp = (
        datetime.now(timezone.utc)
        + timedelta(seconds=CONSENT_TOKEN_TTL_SECONDS)
    ).isoformat()
    payload = {
        "dyad_id": dyad_id,
        "memory_id": memory_id,
        "action": action,
        "nonce": secrets.token_urlsafe(16),
        "exp": exp,
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    sig = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return f"{_b64u(payload_bytes)}.{_b64u(sig)}"


def verify_token(
    token: str,
    *,
    expected_dyad_id: str,
    expected_memory_id: int | None,
    expected_action: str,
) -> dict:
    """Verify a consent token. Raises CamaAPIError on any failure.
    On success, marks the nonce as consumed (one-shot) and returns
    the decoded payload."""
    init_consent_schema()
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload_bytes = _b64u_decode(payload_b64)
        sig = _b64u_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        raise CamaAPIError(403, CamaContract.CONSENT_TOKEN_MISMATCH,
                           detail="malformed consent token")

    expected_sig = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        raise CamaAPIError(403, CamaContract.CONSENT_TOKEN_MISMATCH,
                           detail="consent token signature invalid")

    payload = json.loads(payload_bytes.decode("utf-8"))
    # TTL
    exp = datetime.fromisoformat(payload["exp"])
    if exp < datetime.now(timezone.utc):
        raise CamaAPIError(403, CamaContract.CONSENT_TOKEN_EXPIRED)
    # Binding checks
    if payload["dyad_id"] != expected_dyad_id:
        raise CamaAPIError(403, CamaContract.CONSENT_TOKEN_MISMATCH,
                           detail="consent token bound to a different dyad")
    if payload["memory_id"] != expected_memory_id:
        raise CamaAPIError(403, CamaContract.CONSENT_TOKEN_MISMATCH,
                           detail="consent token bound to a different memory")
    if payload["action"] != expected_action:
        raise CamaAPIError(403, CamaContract.CONSENT_TOKEN_MISMATCH,
                           detail=f"consent token action mismatch: {payload['action']!r}")

    # One-shot enforcement via consumed-nonce table
    c = _open_keys_db()
    try:
        try:
            c.execute(
                "INSERT INTO consent_consumed "
                "(nonce, dyad_id, memory_id, action, consumed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    payload["nonce"],
                    payload["dyad_id"],
                    payload["memory_id"],
                    payload["action"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            c.commit()
        except sqlite3.IntegrityError:
            raise CamaAPIError(
                403, CamaContract.CONSENT_TOKEN_MISMATCH,
                detail="consent token already used (one-shot)",
            )
    finally:
        c.close()

    return payload


__all__ = [
    "CONSENT_TOKEN_TTL_SECONDS",
    "CONSENT_ACTIONS",
    "init_consent_schema",
    "mint_token",
    "verify_token",
]
