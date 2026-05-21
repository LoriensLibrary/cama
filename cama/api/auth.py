"""Bearer-token authentication for the CAMA HTTP API.

Design commitments (per API.md § 6 and THREAT_MODEL.md):

  * Keys stored as Argon2id hashes — plaintext exists only in memory
    during creation, never persisted.
  * Constant-time validation — the verification path runs the same
    Argon2 work whether or not a candidate key matches, defending
    against timing oracles on key prefixes (threat #15).
  * Dyad scoping enforced at the middleware layer — every request
    arrives with an ``AuthContext`` carrying ``dyad_id``, and SQL
    paths use that ID as a non-optional filter (threat #2).
  * No HTTP path to operator-level access — those endpoints live in
    the ``cama-ops`` CLI with file-based auth (API.md § 3).

The keys DB is a separate SQLite file (``CAMA_API_KEY_DB``) so a
compromised memories DB doesn't expose keys, and so backup retention
policy can differ between the two.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from cama.api.errors import CamaAPIError, CamaContract

# ---------------------------------------------------------------------------
# Keys storage
# ---------------------------------------------------------------------------
KeyKind = Literal["live", "dev", "tenant"]

# Argon2id parameters per OWASP 2023 password-storage guidance.
# These are heavier than typical web defaults because keys are
# high-value and validation latency is small per request.
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# A precomputed dummy hash used by the constant-time path when no
# candidate key is found. Computing this lazily once at import time
# keeps the per-request cost predictable.
_DUMMY_HASH = _HASHER.hash("no-key-found-constant-time-defense")


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The result of a successful bearer-token validation.

    Carries the authorized dyad ID and the key's metadata so handlers
    can scope SQL and audit-log correctly.
    """

    dyad_id: str
    key_fingerprint: str
    kind: KeyKind
    key_id: int


def _open_keys_db() -> sqlite3.Connection:
    path = os.environ.get(
        "CAMA_API_KEY_DB",
        os.path.expanduser("~/.cama/api_keys.db"),
    )
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_keys_schema() -> None:
    """Create the keys + audit-log tables if missing. Idempotent.

    Two tables, both append-only at the API layer:
      * api_keys           — one row per issued key, with the Argon2 hash
      * api_audit_log      — one row per request (THREAT_MODEL #13)
    """
    c = _open_keys_db()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                hash TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('live','dev','tenant')),
                dyad_id TEXT NOT NULL,
                name TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_at TEXT,
                last_used_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_api_keys_fingerprint
                ON api_keys(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_api_keys_dyad
                ON api_keys(dyad_id);

            CREATE TABLE IF NOT EXISTS api_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                key_fingerprint TEXT,
                dyad_id TEXT,
                http_method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                latency_ms REAL,
                request_body_hash TEXT,
                error_code TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON api_audit_log(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_key
                ON api_audit_log(key_fingerprint);
        """)
        c.commit()
    finally:
        c.close()


def _fingerprint(plaintext_key: str) -> str:
    """Return a non-secret short identifier for a key (the first 12 chars
    of its hash prefix). Used as a stable lookup index so the audit log
    can reference a key without storing the plaintext."""
    # We use the first three '_'-delimited segments + a public-derived
    # short hash of the random portion. Specifically: ``cama_sk_live_<r>``
    # has random portion ``r``; the fingerprint is the first 12 chars of
    # SHA-256(r). This is deterministic and key-prefix-stable.
    import hashlib

    parts = plaintext_key.split("_", 3)
    if len(parts) != 4 or parts[0] != "cama" or parts[1] != "sk":
        # Caller will get KEY_INVALID downstream; we still need *some*
        # fingerprint for the dummy hash path so the audit log has a
        # consistent shape.
        return "invalid_prefix"
    random_portion = parts[3]
    digest = hashlib.sha256(random_portion.encode("ascii")).hexdigest()
    return digest[:12]


def create_key(
    *,
    dyad_id: str,
    kind: KeyKind = "live",
    name: str | None = None,
    expires_at: str | None = None,
) -> tuple[str, str]:
    """Mint a new key. Returns ``(plaintext_key, fingerprint)``.

    The plaintext is shown to the caller exactly once. After this
    function returns, only the Argon2id hash and the fingerprint are
    persisted; the plaintext cannot be recovered.
    """
    if kind not in ("live", "dev", "tenant"):
        raise ValueError(f"unknown key kind: {kind!r}")

    random_portion = secrets.token_urlsafe(32)
    plaintext = f"cama_sk_{kind}_{random_portion}"
    fingerprint = _fingerprint(plaintext)
    hashed = _HASHER.hash(plaintext)
    now = _now_iso()

    init_keys_schema()
    c = _open_keys_db()
    try:
        c.execute(
            "INSERT INTO api_keys "
            "(fingerprint, hash, kind, dyad_id, name, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fingerprint, hashed, kind, dyad_id, name, now, expires_at),
        )
        c.commit()
    finally:
        c.close()
    return plaintext, fingerprint


def revoke_key(fingerprint: str) -> bool:
    """Mark a key revoked. Returns True if a row was updated."""
    c = _open_keys_db()
    try:
        cur = c.execute(
            "UPDATE api_keys SET revoked_at = ? "
            "WHERE fingerprint = ? AND revoked_at IS NULL",
            (_now_iso(), fingerprint),
        )
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def validate_bearer(token: str) -> AuthContext:
    """Verify a bearer token and return its AuthContext.

    Raises:
        CamaAPIError(401, KEY_INVALID) if the token is malformed.
        CamaAPIError(401, KEY_REVOKED) if the token was revoked.
        CamaAPIError(401, KEY_EXPIRED) if the token expired.
        CamaAPIError(401, UNAUTHORIZED) if the hash does not verify.

    The verification path runs Argon2 against either the candidate
    row's hash *or* the precomputed dummy hash, so wall-clock latency
    is approximately independent of whether the candidate exists
    (threat #15).
    """
    if not token or not token.startswith("cama_sk_"):
        # Even on malformed input we run one Argon2 op to keep timing
        # opaque before raising.
        try:
            _HASHER.verify(_DUMMY_HASH, token or "")
        except VerifyMismatchError:
            pass
        raise CamaAPIError(401, CamaContract.KEY_INVALID)

    fp = _fingerprint(token)
    c = _open_keys_db()
    try:
        row = c.execute(
            "SELECT id, hash, kind, dyad_id, revoked_at, expires_at "
            "FROM api_keys WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
    finally:
        c.close()

    # Constant-time-ish: do an Argon2 verify whether or not we found a
    # row. If no row, verify against the dummy hash.
    candidate_hash = row["hash"] if row else _DUMMY_HASH
    try:
        _HASHER.verify(candidate_hash, token)
        verified = bool(row)
    except VerifyMismatchError:
        verified = False

    if not verified:
        raise CamaAPIError(401, CamaContract.UNAUTHORIZED)

    if row["revoked_at"]:
        raise CamaAPIError(401, CamaContract.KEY_REVOKED)

    if row["expires_at"] and row["expires_at"] < _now_iso():
        raise CamaAPIError(401, CamaContract.KEY_EXPIRED)

    # Bump last_used_at (best-effort; non-blocking on error)
    try:
        c = _open_keys_db()
        c.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (_now_iso(), row["id"]),
        )
        c.commit()
        c.close()
    except sqlite3.Error:
        pass

    return AuthContext(
        dyad_id=row["dyad_id"],
        key_fingerprint=fp,
        kind=row["kind"],
        key_id=row["id"],
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def write_audit(
    *,
    key_fingerprint: str | None,
    dyad_id: str | None,
    http_method: str,
    endpoint: str,
    status_code: int,
    latency_ms: float,
    request_body_hash: str | None,
    error_code: str | None,
) -> None:
    """Append an audit row. The body itself is never logged — only its
    SHA-256 hash, for replay-detection in operator review."""
    try:
        c = _open_keys_db()
        c.execute(
            "INSERT INTO api_audit_log "
            "(ts, key_fingerprint, dyad_id, http_method, endpoint, "
            "status_code, latency_ms, request_body_hash, error_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                key_fingerprint,
                dyad_id,
                http_method,
                endpoint,
                status_code,
                latency_ms,
                request_body_hash,
                error_code,
            ),
        )
        c.commit()
        c.close()
    except sqlite3.Error:
        # Audit log writes are best-effort — a failed audit row must
        # not break the request. Operators should monitor for
        # api_audit_log row gaps.
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    from cama.core.time_utils import now_iso

    return now_iso()


# Re-export the dummy hash so tests can prove timing parity.
DUMMY_HASH = _DUMMY_HASH
