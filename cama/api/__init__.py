"""CAMA HTTP API, v1.

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
    cama.api.deps      Shared helpers + the ``require_auth`` dependency
                       every router takes. Single source of truth for
                       the memory-DB connection, the dyad-column
                       migration, the row-to-response mapper, and the
                       ``is_negative_affect`` safety predicate.
    cama.api.server    Lean application factory: lifespan hook,
                       audit middleware, three exception handlers that
                       route every error through the 7807 envelope, and
                       ``include_router`` for each routers/* module.
                       Endpoint handlers themselves live in routers/*.
    cama.api.routers   One module per endpoint family, health,
                       memories, search, threads, dyads, webhooks,
                       consent. Each module exposes a ``router``
                       attribute the factory mounts.
    cama.api.webhooks  Subscription CRUD + signed delivery + audit
                       log. The router file under routers/ owns the
                       HTTP surface; this module owns the delivery
                       mechanism the rest of the API calls into via
                       ``notify()``.
    cama.api.consent   HMAC-SHA256 one-shot consent tokens with
                       5-minute TTL, bound to (dyad_id, memory_id,
                       action) triples, replay-protected via a
                       ``consent_consumed`` nonce table.

The v1 design commitments in ``API.md`` § 2 are enforced here:
provenance NOT NULL, inferences cannot self-promote, dyad isolation,
counterweight injection on by default, real delete.
"""
