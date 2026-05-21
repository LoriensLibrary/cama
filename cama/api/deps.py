"""Shared helpers + dependencies for the CAMA HTTP API routers.

This module is the single source of truth for the things every router
needs: the auth dependency, the memory-DB connection opener, the
idempotent dyad-column migration, and the safety predicate that fires
counterweight injection. Moving them here lets each router stay
narrowly focused on its own endpoint family — and keeps the app
factory in ``server.py`` short enough to read in one screen.

Naming convention: public names in this module do NOT carry a leading
underscore — they are explicitly part of the cross-router contract.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import Header, Request

from cama.api.auth import AuthContext, validate_bearer
from cama.api.errors import CamaAPIError, CamaContract
from cama.api.schemas import Affect, MemoryResponse

# ---------------------------------------------------------------------------
# Constants — shared across routers
# ---------------------------------------------------------------------------
API_VERSION = "1.26.0"
DEFAULT_DYAD_ID = "default"
COUNTERWEIGHT_NEG_VALENCE_THRESHOLD = -0.5
COUNTERWEIGHT_NEG_EMOTIONS = {
    "grief",
    "fear",
    "shame",
    "sadness",
    "anger",
    "loneliness",
    "betrayal",
    "exhaustion",
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def open_memory_db() -> sqlite3.Connection:
    """Open the memory SQLite file as a row-factory connection.

    Reads ``CAMA_DB_PATH`` at call time (not import time) so test
    fixtures that swap the env var per-test land in the right DB.
    """
    path = os.environ.get(
        "CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db")
    )
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_dyad_column() -> None:
    """Idempotent migration: add a ``dyad_id`` column to ``memories``
    if it's missing. Existing rows are tagged with the default-dyad
    sentinel so pre-multi-tenant data is still reachable by the
    default-dyad key.

    Best-effort: a read-only or temporarily-locked DB will not break
    startup; handlers themselves still scope correctly as long as the
    column exists.
    """
    c = open_memory_db()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(memories)").fetchall()}
        if "dyad_id" not in cols:
            c.execute(
                "ALTER TABLE memories ADD COLUMN dyad_id TEXT NOT NULL "
                "DEFAULT 'default'"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_dyad "
                "ON memories(dyad_id)"
            )
            c.commit()
    except sqlite3.Error:
        # Memory DB may not be writable at startup (test fixtures init
        # their own schema). Migration is best-effort here.
        pass
    finally:
        c.close()


def row_to_memory(row: sqlite3.Row, dyad_id: str) -> MemoryResponse:
    """Map a raw ``memories`` row to the API's canonical response shape."""
    affect = None
    # memory_affect rows live in a separate table; we leave fetching
    # them as a downstream concern. The schema permits null.
    return MemoryResponse(
        id=row["id"],
        dyad_id=dyad_id,
        text=row["raw_text"] or "",
        memory_type=row["memory_type"],
        proposed_by=row["proposed_by"],
        source_type=row["source_type"],
        status=row["status"],
        consent_level=row["consent_level"] or "medium",
        context=row["context"],
        affect=affect,
        is_core=bool(row["is_core"]) if row["is_core"] is not None else False,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        review_after=row["review_after"],
    )


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """FastAPI dependency that extracts and validates the bearer token.

    Stores the resulting ``AuthContext`` on ``request.state.auth`` so
    the audit middleware can read it for logging.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise CamaAPIError(401, CamaContract.UNAUTHORIZED)
    token = authorization.split(None, 1)[1].strip()
    ctx = validate_bearer(token)
    request.state.auth = ctx
    return ctx


# ---------------------------------------------------------------------------
# Safety predicate — counterweight-injection gate
# ---------------------------------------------------------------------------
def is_negative_affect(affect: Affect | None) -> bool:
    """Reproduce the CAMA anti-spiral predicate at the API boundary so
    counterweight injection runs even when downstream callers haven't
    computed affect themselves.

    Two conditions trigger injection (either is sufficient):
      * Dimensional: ``valence <= -0.5``.
      * Categorical: sum of weights for emotions in the negative chord
        set exceeds 1.5.
    """
    if affect is None:
        return False
    if affect.valence <= COUNTERWEIGHT_NEG_VALENCE_THRESHOLD:
        return True
    neg_sum = sum(
        v for k, v in affect.emotions.items() if k in COUNTERWEIGHT_NEG_EMOTIONS
    )
    return neg_sum > 1.5


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def iso_days_from_now(days: int) -> str:
    """ISO-8601 timestamp ``days`` days from now (UTC)."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
