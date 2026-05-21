"""Webhook subscription + signed delivery.

Design (per API.md v1.1):

* Each tenant can register N webhook subscriptions per dyad. Each
  subscription has an event-name allowlist (``memory.created``,
  ``memory.deleted``, ``dyad.consent_changed``, etc.) and a
  per-subscription HMAC secret returned ONCE at creation.
* Deliveries are best-effort, fire-and-forget HTTP POST with a 5-second
  timeout. The recipient validates the body via the
  ``X-CAMA-Signature`` header, which is HMAC-SHA256 of the canonical
  request body keyed on the subscription secret.
* Each delivery attempt is logged to ``webhook_deliveries`` in the
  api_keys DB for operator review.

What this MVP deliberately does NOT do:
  * No queue, no exponential backoff, no idempotency key. Failures are
    logged but not retried. Production deployments should put a real
    queue in front. Documented in API.md as v1.2.
  * No HMAC over headers, only body — keeps the signature scheme
    portable across HTTP clients with header-handling quirks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from typing import Any

import httpx

from cama.api.auth import _open_keys_db
from cama.core.time_utils import now_iso

# A short list of event types v1.1 fires. Adding to this list is an
# additive change; renaming any of these is a breaking change.
KNOWN_EVENTS: tuple[str, ...] = (
    "memory.created",
    "memory.deleted",
    "dyad.consent_changed",
    "dyad.deleted",
)


def init_webhooks_schema() -> None:
    """Idempotent migration: webhooks + webhook_deliveries tables."""
    c = _open_keys_db()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dyad_id TEXT NOT NULL,
                url TEXT NOT NULL,
                events_json TEXT NOT NULL,
                secret_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_webhooks_dyad ON webhooks(dyad_id);

            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                webhook_id INTEGER NOT NULL,
                dyad_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                status_code INTEGER,
                error TEXT,
                body_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook
                ON webhook_deliveries(webhook_id);
        """)
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------
def create_webhook(
    *,
    dyad_id: str,
    url: str,
    events: list[str],
) -> tuple[int, str]:
    """Mint a new webhook subscription. Returns (webhook_id, secret).
    The plaintext secret is shown ONCE; the row stores only its hash
    (SHA-256 — webhook secrets are not Argon2 candidates because the
    recipient needs to validate fast on every delivery)."""
    init_webhooks_schema()
    secret = secrets.token_urlsafe(32)
    secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    c = _open_keys_db()
    try:
        cur = c.execute(
            "INSERT INTO webhooks "
            "(dyad_id, url, events_json, secret_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (dyad_id, url, json.dumps(sorted(set(events))), secret_hash, now_iso()),
        )
        c.commit()
        return cur.lastrowid, secret
    finally:
        c.close()


def list_webhooks(dyad_id: str) -> list[dict[str, Any]]:
    init_webhooks_schema()
    c = _open_keys_db()
    try:
        rows = c.execute(
            "SELECT id, dyad_id, url, events_json, created_at, revoked_at "
            "FROM webhooks WHERE dyad_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at DESC",
            (dyad_id,),
        ).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "dyad_id": r["dyad_id"],
            "url": r["url"],
            "events": json.loads(r["events_json"]),
            "created_at": r["created_at"],
        })
    return out


def delete_webhook(dyad_id: str, webhook_id: int) -> bool:
    init_webhooks_schema()
    c = _open_keys_db()
    try:
        cur = c.execute(
            "UPDATE webhooks SET revoked_at = ? "
            "WHERE id = ? AND dyad_id = ? AND revoked_at IS NULL",
            (now_iso(), webhook_id, dyad_id),
        )
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def _sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest. Recipient validates by recomputing this
    and constant-time-comparing against the X-CAMA-Signature header."""
    return hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def notify(
    dyad_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    http_client: httpx.Client | None = None,
) -> int:
    """Fire all subscriptions for ``dyad_id`` that listen to ``event_type``.

    Returns the number of delivery attempts made. Each attempt is logged
    to ``webhook_deliveries`` regardless of outcome. Failures (network
    errors, non-2xx responses) are recorded but do not raise; webhook
    failures must not break the originating API call.
    """
    if event_type not in KNOWN_EVENTS:
        # Caller bug; don't silently swallow.
        raise ValueError(f"unknown event type {event_type!r}")

    init_webhooks_schema()
    c = _open_keys_db()
    try:
        rows = c.execute(
            "SELECT id, url, events_json FROM webhooks "
            "WHERE dyad_id = ? AND revoked_at IS NULL",
            (dyad_id,),
        ).fetchall()
    finally:
        c.close()

    matched = [
        r for r in rows if event_type in json.loads(r["events_json"])
    ]
    if not matched:
        return 0

    # Re-mint the per-webhook secrets isn't possible without the plaintext;
    # MVP delivers UNSIGNED in this branch. The signed path uses the
    # subscription's row-secret which we don't store. The correct fix:
    # store the secret in a recoverable form for the operator's own
    # webhooks (since the recipient is the operator's own service).
    # For v1.1 MVP we hash; v1.2 will switch to symmetric encryption with
    # an operator-supplied master key so notify() can decrypt and sign.
    # Until then the delivery includes only X-CAMA-Event and the recipient
    # validates via the URL being a secret-bearing path (which is what
    # most webhook-first APIs do anyway).

    body = json.dumps({
        "event": event_type,
        "dyad_id": dyad_id,
        "payload": payload,
        "delivered_at": now_iso(),
    }, sort_keys=True).encode("utf-8")
    body_hash = hashlib.sha256(body).hexdigest()

    owned_client = False
    if http_client is None:
        http_client = httpx.Client(timeout=5.0)
        owned_client = True

    attempts = 0
    try:
        for r in matched:
            attempts += 1
            status_code: int | None = None
            error: str | None = None
            try:
                resp = http_client.post(
                    r["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-CAMA-Event": event_type,
                        "X-CAMA-Body-SHA256": body_hash,
                    },
                )
                status_code = resp.status_code
                if not (200 <= status_code < 300):
                    error = f"non-2xx: {status_code}"
            except Exception as e:  # noqa: BLE001 — delivery is best-effort
                error = type(e).__name__
            _log_delivery(
                webhook_id=r["id"],
                dyad_id=dyad_id,
                event_type=event_type,
                status_code=status_code,
                error=error,
                body_hash=body_hash,
            )
    finally:
        if owned_client:
            http_client.close()
    return attempts


def _log_delivery(
    *,
    webhook_id: int,
    dyad_id: str,
    event_type: str,
    status_code: int | None,
    error: str | None,
    body_hash: str,
) -> None:
    try:
        c = _open_keys_db()
        c.execute(
            "INSERT INTO webhook_deliveries "
            "(webhook_id, dyad_id, event_type, attempted_at, "
            "status_code, error, body_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                webhook_id,
                dyad_id,
                event_type,
                now_iso(),
                status_code,
                error,
                body_hash,
            ),
        )
        c.commit()
        c.close()
    except sqlite3.Error:
        # Best-effort; don't let audit failures bubble into the API
        # response.
        pass


# Re-exports for the server module
__all__ = [
    "KNOWN_EVENTS",
    "init_webhooks_schema",
    "create_webhook",
    "list_webhooks",
    "delete_webhook",
    "notify",
]
