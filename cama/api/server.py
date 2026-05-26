"""CAMA HTTP API, v1 application factory.

This module is the application *factory* for the CAMA v1 API. It does
exactly four things:

  1. Defines the lifespan hook (idempotent schema migrations on
     startup: keys, dyad column, webhooks, consent).
  2. Wires the audit middleware (request ID + audit-log row per
     request, even on failure).
  3. Installs the three exception handlers that map every error path
     through the RFC 7807 problem-details envelope so the SDK can
     branch on ``cama.violated_contract``.
  4. ``include_router(...)`` every router under ``cama.api.routers.*``.

Endpoint handlers do NOT live in this file. They live in
``cama/api/routers/`` (one module per endpoint family). Shared helpers
(``require_auth``, ``open_memory_db``, ``is_negative_affect``, etc.)
live in ``cama/api/deps.py``.

Running locally::

    pip install "cama[api]"
    export CAMA_DB_PATH=/path/to/memory.db
    export CAMA_API_KEY_DB=/path/to/api_keys.db
    cama-api-server --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from cama.api.auth import AuthContext, init_keys_schema, write_audit
from cama.api.consent import init_consent_schema
from cama.api.deps import API_VERSION, ensure_dyad_column
from cama.api.errors import CamaAPIError, CamaContract, to_problem
from cama.api.routers import (
    consent as consent_router,
)
from cama.api.routers import (
    dyads as dyads_router,
)
from cama.api.routers import (
    health as health_router,
)
from cama.api.routers import (
    memories as memories_router,
)
from cama.api.routers import (
    search as search_router,
)
from cama.api.routers import (
    threads as threads_router,
)
from cama.api.routers import (
    webhooks as webhooks_router,
)
from cama.api.webhooks import init_webhooks_schema


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize keys DB schema (idempotent)
    init_keys_schema()
    # Idempotent migration of the memories table for multi-tenant dyad scoping
    ensure_dyad_column()
    # Webhooks subsystem (subscriptions + delivery log)
    init_webhooks_schema()
    # Consent-token consumed-nonce table
    init_consent_schema()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="CAMA API",
        version=API_VERSION,
        description=(
            "Persistent, emotionally-indexed, provenance-aware memory "
            "infrastructure for AI applications. See API.md and "
            "THREAT_MODEL.md for the contract and the threat model."
        ),
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url="/v1/redoc",
        lifespan=lifespan,
    )

    # ----------------- middleware: request ID + audit log ------------------
    @app.middleware("http")
    async def audit_middleware(request: Request, call_next):
        request_id = f"req_{uuid.uuid4().hex[:24]}"
        request.state.request_id = request_id
        t0 = time.perf_counter()
        body_hash: str | None = None
        if request.method in {"POST", "PATCH", "DELETE"}:
            body = await request.body()
            # Re-attach the body so downstream handlers can still read it.

            async def receive():  # type: ignore[no-untyped-def]
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = receive  # type: ignore[attr-defined]
            if body:
                body_hash = hashlib.sha256(body).hexdigest()

        try:
            response = await call_next(request)
            error_code = None
        except CamaAPIError as e:
            response = to_problem(e, request)
            error_code = e.contract.value
        latency_ms = (time.perf_counter() - t0) * 1000.0

        auth: AuthContext | None = getattr(request.state, "auth", None)
        write_audit(
            key_fingerprint=auth.key_fingerprint if auth else None,
            dyad_id=auth.dyad_id if auth else None,
            http_method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
            request_body_hash=body_hash,
            error_code=error_code,
        )
        response.headers["X-Request-Id"] = request_id
        return response

    # ----------------- exception handlers ----------------------------------
    @app.exception_handler(CamaAPIError)
    async def _cama_error_handler(request: Request, exc: CamaAPIError):
        return to_problem(exc, request)

    @app.exception_handler(HTTPException)
    async def _http_error_handler(request: Request, exc: HTTPException):
        # Map raw FastAPI HTTPExceptions through the 7807 envelope too.
        contract = CamaContract.UNAUTHORIZED
        if exc.status_code == 422:
            contract = CamaContract.ENUM_VALUE_UNKNOWN
        elif exc.status_code == 404:
            contract = CamaContract.DYAD_SCOPE
        return to_problem(
            CamaAPIError(
                exc.status_code,
                contract,
                detail=str(exc.detail) if exc.detail else None,
            ),
            request,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ):
        # Pydantic validation errors land here. We map them to the 7807
        # envelope so the SDK can branch on cama.violated_contract.
        # Choose the most specific contract code by inspecting the
        # first error's location: if it's about proposed_by or
        # source_type, surface as provenance_required; otherwise it's
        # an enum or shape violation.
        contract = CamaContract.ENUM_VALUE_UNKNOWN
        errors = exc.errors()
        if errors:
            first_loc = errors[0].get("loc", [])
            if any(
                str(part) in {"proposed_by", "source_type"}
                for part in first_loc
            ):
                contract = CamaContract.PROVENANCE_REQUIRED
        return to_problem(
            CamaAPIError(
                422,
                contract,
                detail=(
                    "Request body failed validation. See the OpenAPI "
                    "schema for the canonical field set and enum values."
                ),
                extra={"validation_errors": exc.errors()},
            ),
            request,
        )

    # ----------------- routers ---------------------------------------------
    # Order matches the conceptual flow a new reader will follow:
    # meta -> memories (CRUD) -> search -> threads -> dyads -> webhooks
    # -> consent (orthogonal flow). FastAPI route resolution is
    # path+method based, so this ordering is for human readability.
    app.include_router(health_router.router)
    app.include_router(memories_router.router)
    app.include_router(search_router.router)
    app.include_router(threads_router.router)
    app.include_router(dyads_router.router)
    app.include_router(webhooks_router.router)
    app.include_router(consent_router.router)

    return app


# Module-level app so ``uvicorn cama.api.server:app`` works out of the box.
app = create_app()


def main() -> int:
    """Console-script entry point: ``cama-api-server``."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="CAMA HTTP API server (v1)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "cama.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
