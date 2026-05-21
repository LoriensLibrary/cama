"""``/v1/health`` and ``/v1/version`` — meta endpoints (no auth).

Health is degraded-mode aware: a missing embedding model is reported
as ``degraded=True`` with ``status="degraded"`` rather than ``"down"``,
because the API still serves memory CRUD without embeddings — only
semantic search is impacted.
"""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter

from cama.api.deps import API_VERSION, open_memory_db
from cama.api.schemas import HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_status: str = "ok"
    try:
        c = open_memory_db()
        c.execute("SELECT 1").fetchone()
        c.close()
    except sqlite3.Error:
        db_status = "down"

    # Embedding model availability is a runtime question; for v1 we
    # report based on a simple env-var hint. A full implementation
    # would ping the embedding worker.
    embedding_provider = os.environ.get("CAMA_EMBEDDING_PROVIDER", "local")
    embedding_model = "ok"
    if embedding_provider == "none":
        embedding_model = "unavailable"
    degraded = db_status != "ok" or embedding_model == "unavailable"
    overall = "ok" if not degraded else "degraded"
    if db_status == "down":
        overall = "down"
    return HealthResponse(
        status=overall,  # type: ignore[arg-type]
        db=db_status,  # type: ignore[arg-type]
        embedding_model=embedding_model,  # type: ignore[arg-type]
        embedding_provider=embedding_provider,
        embedding_model_age_sec=None,
        degraded=degraded,
        version=API_VERSION,
    )


@router.get("/v1/version")
def version() -> dict[str, str]:
    return {"version": API_VERSION, "api": "v1"}
