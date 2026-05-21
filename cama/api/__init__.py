"""CAMA HTTP API — v1.

The public surface that lets the CAMA memory architecture be embedded in
any AI application, not just Claude Desktop via MCP. See ``API.md`` for
the contract and ``THREAT_MODEL.md`` for the attack-mitigation matrix.

Module map:
    cama.api.errors    RFC 7807 problem details + the closed set of
                       ``cama.violated_contract`` codes that distinguish
                       CAMA architectural violations from generic HTTP
                       errors.
    cama.api.schemas   Pydantic models for the request/response shapes.
                       Enums are ``typing.Literal[...]`` so strict
                       validation rejects unknown values at the API
                       boundary instead of leaking them into the store.
    cama.api.auth      Bearer-token validation. Argon2id hash at rest,
                       constant-time verification regardless of whether
                       a candidate key was found, dyad-scoping
                       middleware.
    cama.api.server    FastAPI application; v1 endpoints; lifespan hook
                       that opens the keys DB and warm-loads the
                       embedding model.

The v1 design commitments in ``API.md`` § 2 are enforced here:
provenance NOT NULL, inferences cannot self-promote, dyad isolation,
counterweight injection on by default, real delete.
"""
