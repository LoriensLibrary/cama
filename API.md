# CAMA HTTP API — v1

The public surface that lets CAMA be embedded in any AI application, not just Claude Desktop via MCP.

This document is the contract: what the endpoints are, what they accept, what they return, what they refuse, and what architectural guarantees the API makes that a developer can build on. Read [`RETRIEVAL.md`](RETRIEVAL.md) for the retrieval algorithm itself, [`ARCHITECTURE.md`](ARCHITECTURE.md) for the underlying memory model, [`MULTI_TENANT.md`](MULTI_TENANT.md) for the dyad isolation contract this API exposes, and [`THREAT_MODEL.md`](THREAT_MODEL.md) for the attacks this API is designed against.

---

## 1. Product positioning

**v1 ships self-hosted single-tenant.** A company runs one CAMA instance, one SQLite store, one shared dyad. Auth is an API key per app. The pitch is "Redis for persistent emotionally-indexed memory" — one process, one config file, one Docker image.

**Multi-tenant is exposed via the dyad model.** The same CAMA architecture supports many users (each user = a dyad with its own consent state, hive signing salt, identity teachings). The API surface for that is already in this document — the difference is operational, not architectural.

**Hosted SaaS is not v1.** That's billing, isolation guarantees, SOC 2, on-call rotation — a company-building conversation, not engineering work. The API is designed so a hosted operator can layer those concerns on top, but no v1 endpoint depends on them.

---

## 2. Architectural commitments — what the API refuses to compromise

These are the contracts that distinguish CAMA from a generic memory API. The API enforces them at the boundary; a caller cannot bypass them by malformed input.

1. **Provenance is required on every write.** `POST /v1/memories` rejects (HTTP 422) any payload missing both `proposed_by` (`user` | `assistant` | `system`) and `source_type` (`teaching` | `inference` | `exchange`). No defaults that obscure intent.
2. **Inferences cannot self-promote.** A write with `proposed_by=assistant` AND `source_type=inference` is stored with `status=provisional` and a TTL. Promotion to `durable` requires `PATCH /v1/memories/{id}/confirm` with a *user-authored token* from an interactive consent challenge (see §6.3).
3. **Dyad isolation is absolute.** Every endpoint reads the auth context's dyad scope and runs SQL with that scope. There is no API path that returns memories from a dyad the caller is not authorized for. A tenant key that created N dyads can act on those N — not on dyads it did not create.
4. **Counterweight injection is on by default.** `POST /v1/search` runs the anti-spiral logic for queries with strongly-negative affect (see [RETRIEVAL.md § 4](RETRIEVAL.md#4--counterweight-injection--anti-spiral-protection)). This cannot be turned off per-request. It can be disabled at the *dyad* level via `consent.counterweights_enabled=false`, which writes an audit row and surfaces in `GET /v1/dyads/{id}`. The dyad owner has to opt out knowingly.
5. **Right to delete is real.** `DELETE /v1/dyads/{id}` (with double-confirm) performs an actual filesystem + DB wipe and returns a deletion manifest (counts + Merkle root of deleted IDs — IDs themselves are not leaked in the manifest).

If a future version of this API loosens any of those five, it must be at `/v2/` with a documented migration and the old version supported in parallel for six months minimum.

---

## 3. Resource model

Eight resources. RESTful. JSON in, JSON out. All paths versioned at `/v1/`.

| Resource | Endpoints |
|---|---|
| `memories` | `POST /v1/memories`, `GET /v1/memories/{id}`, `DELETE /v1/memories/{id}`, `PATCH /v1/memories/{id}/confirm` |
| `search` | `POST /v1/search` |
| `threads` | `POST /v1/thread/start`, `POST /v1/thread/end` |
| `dyads` | `GET /v1/dyads/{id}`, `PATCH /v1/dyads/{id}/consent`, `DELETE /v1/dyads/{id}`, `GET /v1/dyads/{id}/export` |
| `consent` | `POST /v1/consent/challenge`, `POST /v1/consent/grant` |
| `health` | `GET /v1/health` |
| `meta` | `GET /v1/openapi.json`, `GET /v1/version` |

Out of the API entirely (operator-only, file-based auth at `~/.cama-admin/token`, accessible via `cama-ops` CLI):

```
cama-ops list-dyads
cama-ops audit-log --since=...
cama-ops backup
cama-ops keys create --dyad=<id> --kind=live|dev
cama-ops keys list
cama-ops keys revoke <key-fingerprint>
```

The ops surface is **deliberately not an HTTP endpoint.** Operator-level access via HTTP enlarges the attack surface against the most privileged identity. File-based auth requires shell access to the host.

---

## 4. The endpoint reference

### 4.1 `POST /v1/memories` — store a memory

```http
POST /v1/memories
Authorization: Bearer cama_sk_live_...
Content-Type: application/json

{
  "text": "the user prefers concise summaries with citations",
  "memory_type": "teaching",
  "proposed_by": "user",
  "source_type": "teaching",
  "context": "preference inferred from explicit feedback in session 2026-05-21",
  "affect": {
    "valence": 0.2,
    "arousal": 0.1,
    "emotions": { "trust": 0.6, "recognition": 0.4 }
  },
  "memory_kind": "preference"
}
```

**201 Created** with the stored record:

```json
{
  "id": 53104,
  "dyad_id": "dyad_8f3a...",
  "text": "the user prefers concise summaries with citations",
  "memory_type": "teaching",
  "proposed_by": "user",
  "source_type": "teaching",
  "status": "durable",
  "consent_level": "high",
  "created_at": "2026-05-21T19:08:00Z",
  "review_after": null
}
```

**422 Unprocessable Entity** with the standard error envelope (§5) if `proposed_by` or `source_type` is missing, or if `text` is empty, or if `memory_type` is not in the canonical enum (see `GET /v1/openapi.json` for the closed set).

**Inference write behavior:** if `proposed_by=assistant` AND `source_type=inference`, the response includes `status=provisional` and `review_after=<utc-iso-30-days-out>`. The memory is not retrievable as a durable hit until promoted via `PATCH /v1/memories/{id}/confirm`.

### 4.2 `GET /v1/memories/{id}` — read one memory

```http
GET /v1/memories/53104
Authorization: Bearer cama_sk_live_...
```

**200 OK** with the full record + affect block. **404 Not Found** if the ID belongs to a different dyad (deliberately not 403 — does not leak existence). **404** also if the ID does not exist.

### 4.3 `DELETE /v1/memories/{id}` — real delete

```http
DELETE /v1/memories/53104
Authorization: Bearer cama_sk_live_...
X-Confirm: 53104
```

**204 No Content** on success. The X-Confirm header value must equal the path ID to prevent accidental destructive actions from misrouted requests. **400 Bad Request** otherwise.

Deletion is real: the row is removed from `memories`, related `memory_affect` and `memory_embeddings` rows cascade-delete, and `librarian_membership` rows for the deleted memory are cleared. No soft-delete tombstone.

### 4.4 `PATCH /v1/memories/{id}/confirm` — promote provisional → durable

```http
PATCH /v1/memories/53104/confirm
Authorization: Bearer cama_sk_live_...
X-Consent-Token: cons_01HXY...
```

The consent token must come from a successful `POST /v1/consent/grant` for this same dyad + memory ID within the last 5 minutes. **403 Forbidden** if the token is missing, expired, or doesn't bind to this memory.

This is the architectural mechanism enforcing "AI cannot self-promote teachings." The token is one-shot — promoting consumes it.

### 4.5 `POST /v1/search` — blended retrieval

```http
POST /v1/search
Authorization: Bearer cama_sk_live_...
Content-Type: application/json

{
  "query": "what does the user prefer",
  "limit": 10,
  "include_provisional": false
}
```

**200 OK** with the ranked result list, the four-signal blended scores, and metadata about the routing path that fired:

```json
{
  "results": [
    {
      "id": 53104,
      "text": "the user prefers concise summaries...",
      "score": 0.87,
      "score_breakdown": {
        "semantic": 0.92,
        "affect":   0.61,
        "relational": 0.30,
        "recency":  0.99
      },
      "memory_type": "teaching",
      "proposed_by": "user",
      "source_type": "teaching",
      "created_at": "2026-05-21T19:08:00Z"
    }
  ],
  "routing": {
    "phase": "2.6",
    "librarians_activated": 3,
    "counterweights_injected": 0,
    "latency_ms": 158.4
  },
  "warnings": []
}
```

If the query's affect classification triggers the counterweight predicate, the response includes `counterweights_injected > 0` and the supplemented memories are flagged with `_counterweight: true`. The caller can identify them but cannot opt out per-request.

If counterweight injection is disabled at the dyad level (via consent), the response includes `warnings: ["counterweights_disabled_at_dyad_level"]` so the caller knows the safety primitive is off.

### 4.6 `POST /v1/thread/start` — warm boot

```http
POST /v1/thread/start
Authorization: Bearer cama_sk_live_...
Content-Type: application/json

{
  "user_message": "morning, what did we leave off on",
  "user_affect": { "valence": 0.1, "arousal": 0.2 }
}
```

**200 OK** with the dense identity payload (per CAMA's existing `cama_thread_start` MCP tool):

```json
{
  "boot_status": "refreshed",
  "boot_age_min": 2,
  "journal_excerpt": "...",
  "resonant_memories": [ /* top-5 retrieval keyed to user_affect */ ],
  "corrections": [ /* recent active correction patterns */ ],
  "compliance": { /* protocol-tracking snapshot */ },
  "performance_ms": 1963.7
}
```

### 4.7 `POST /v1/thread/end` — close session

Closes the active thread, triggers a fresh `boot_summary.json` regeneration so the next thread/start is fast. No body required.

### 4.8 Dyad endpoints (§4.9–§4.12)

`GET /v1/dyads/{id}` — read consent state, totals, last activity. Returns 404 if not authorized for that dyad.

`PATCH /v1/dyads/{id}/consent` — update consent flags. Requires:
- Bearer token authorized for that dyad
- `X-Confirm: <dyad_id>` header
- `Origin` in the configured allowlist (CSRF defense)

```json
{
  "consent": {
    "counterweights_enabled": false,
    "hive_consume": true,
    "persona_training": false
  },
  "reason": "user opted out of safety supplementation"
}
```

`DELETE /v1/dyads/{id}` — real, permanent wipe. Requires double-confirm:
- `X-Confirm: <dyad_id>`
- `X-Confirm-Again: I-understand-this-is-permanent`

Returns 200 with the deletion manifest (counts + Merkle root of deleted IDs):

```json
{
  "deleted_at": "2026-05-21T19:30:00Z",
  "dyad_id": "dyad_8f3a...",
  "counts": {
    "memories": 1247,
    "edges": 5891,
    "memberships": 3204,
    "affect_rows": 1247
  },
  "deleted_ids_merkle_root": "a3f8...",
  "audit_path": "/var/lib/cama/_wipe_audit/dyad_8f3a..._2026-05-21T19:30:00Z.json"
}
```

The audit file (not exposed via API) contains the full ID list for the operator's records and can be used to verify completeness against backups.

`GET /v1/dyads/{id}/export` — GDPR-style data portability. Returns a JSON bundle of every memory, affect, edge, and consent-history row for the dyad. Streamed to handle large dyads. Response includes `Content-Disposition: attachment; filename="dyad_<id>_export_<utc>.json"`.

### 4.9 `POST /v1/consent/challenge` and `POST /v1/consent/grant` — user-authored consent flow

When `PATCH /v1/memories/{id}/confirm` is needed, the application server first calls `/v1/consent/challenge`:

```http
POST /v1/consent/challenge
Authorization: Bearer cama_sk_live_...
Content-Type: application/json

{ "memory_id": 53104, "action": "promote_to_durable" }
```

The response contains a one-time challenge URL the application redirects the end-user's browser to. The user sees a CAMA-hosted page (or a self-hosted equivalent) showing the memory in question and an Accept/Reject choice. On accept, the user's browser POSTs to `/v1/consent/grant`, which returns the consent token. The application then includes the token in the `PATCH /v1/memories/{id}/confirm` call.

**This is the architectural mechanism enforcing "AI cannot promote its own inferences."** The user has to *see* the inference and explicitly approve it. The challenge URL is signed (HMAC over dyad ID + memory ID + nonce + TTL), tokens have a 5-minute TTL, and each token is one-shot.

### 4.10 `GET /v1/health` — liveness + degradation

```json
{
  "status": "ok",
  "db": "ok",
  "embedding_model": "ok",
  "embedding_provider": "local",
  "embedding_model_age_sec": 1842,
  "degraded": false,
  "version": "1.26.0"
}
```

If the embedding model failed to load, `embedding_model: "unavailable"` and `degraded: true`. In degraded mode, search falls back to keyword-only routing and responses include `warnings: ["search_in_degraded_mode_keyword_only"]`. The API is *not* a 5xx in this state — degraded > failed for memory continuity.

---

## 5. Error model — RFC 7807 with CAMA extension

Every error response uses Problem Details (RFC 7807) plus a `cama` extension that identifies the violated contract:

```json
{
  "type": "https://lorienslibrary.com/cama/errors/provenance-required",
  "title": "Provenance fields missing",
  "status": 422,
  "detail": "POST /v1/memories requires both proposed_by and source_type. See https://docs.lorienslibrary.com/api/v1/memories#provenance.",
  "instance": "req_01HXY7K8E9R0PQAB...",
  "cama": {
    "violated_contract": "provenance_required",
    "fix": "Add { \"proposed_by\": \"user\", \"source_type\": \"teaching\" } to the request body.",
    "doc_url": "https://docs.lorienslibrary.com/api/v1/memories#provenance"
  }
}
```

Defined `cama.violated_contract` values (closed set, in OpenAPI):

| Code | Meaning |
|---|---|
| `provenance_required` | `proposed_by` or `source_type` missing on write |
| `enum_value_unknown` | `memory_type` / `source_type` / `proposed_by` outside the canonical set |
| `dyad_scope` | The requested resource is not in the authenticated dyad's scope |
| `consent_token_required` | Promoting a provisional inference without a valid consent token |
| `consent_token_expired` | Token TTL exceeded (>5 min) |
| `consent_token_mismatch` | Token does not bind to the memory ID being confirmed |
| `confirm_header_missing` | Destructive endpoint called without `X-Confirm` header |
| `origin_not_allowed` | Consent endpoint called from a non-allowlisted Origin |
| `rate_limit_exceeded` | Token bucket empty for this key |
| `key_revoked` | Bearer token is in the revocation list |
| `payload_too_large` | Memory text exceeds 64 KB |
| `dyad_locked` | Dyad is in the middle of a destructive operation (delete in progress) |
| `degraded_mode` | Operation requires a feature unavailable in degraded mode |

The SDK uses `cama.violated_contract` for structured retries and developer-facing messages.

---

## 6. Auth & isolation

### 6.1 Bearer tokens

Two key types, distinguished by prefix:

- `cama_sk_live_...` — long-lived application keys (no expiry)
- `cama_sk_dev_...` — short-lived dev keys (7-day default expiry, configurable up to 30d)

Format: `cama_sk_<env>_<32_url_safe_base64>`. The 32 bytes of entropy give 192-bit randomness, well above brute-force feasibility.

Storage: keys are hashed at rest with **Argon2id** (memory cost 64 MB, time cost 3, parallelism 4 — OWASP 2023 defaults). The plaintext is shown exactly once at creation (in the `cama-ops keys create` output) and never persisted in plaintext.

Validation: incoming bearer is Argon2-verified against each active key's hash. Verifies are constant-time per the Argon2 library.

### 6.2 Two scope models

**Per-dyad keys:** authorize a single dyad. Most common shape — one app = one dyad = one key.

**Tenant keys:** authorize the *set of dyads created by this key*. A consuming application that manages many end-users creates dyads with the tenant key (`POST /v1/dyads` — only callable by a tenant key) and gets per-dyad operating rights for those dyads. The tenant key cannot read across dyads it did not create.

Tenant keys are the multi-tenant story without inventing org/account hierarchies.

### 6.3 Consent token flow

(Detailed in §4.9 above.) Important properties:

- One-shot (single use; promoting consumes the token)
- 5-minute TTL
- Bound to `(dyad_id, memory_id, action)` triple — cannot be reused for a different memory
- Verified via HMAC-SHA256 with a server-side secret
- The user-facing consent page is served on a different Origin than the API itself (CSRF defense)

### 6.4 Rate limits

Tiered by key kind, configurable per key. Defaults:

| Key kind | Steady-state | Burst (30 sec) |
|---|---|---|
| `dev` | 60 req/min | 120 req |
| `live` | 600 req/min | 1200 req |
| Tenant (per dyad) | 600 req/min | 1200 req |
| Operator (file token) | unlimited | n/a |

Rate-limit responses include the standard `RateLimit-*` headers (draft IETF). The token bucket persists in SQLite — a process restart does *not* reset budgets.

### 6.5 CSRF & origin checks

The consent endpoints (`/v1/consent/grant`, `/v1/dyads/{id}/consent`) require:

- Bearer token (always)
- `Origin` header in the configured allowlist
- `X-Confirm` header for destructive operations

Three layers because consent flows are uniquely sensitive to forgery — a malicious site could trick a browser into changing dyad consent flags. The origin allowlist + X-Confirm + bearer token defends.

---

## 7. Embedding compute architecture

The sentence-transformer (`all-MiniLM-L6-v2`) is the most expensive piece of the API at request time. Loading the model is a one-time 7.5-second cost; serving an embedding is 50-150 ms warm.

**v1 design:** model loads in-process with the API server. Single-process concurrency limited by Python's GIL during embedding compute.

**v1.1 design (documented, not built yet):** model runs in a separate worker process (or sidecar container), API server talks to it via a local Unix socket. Allows N API workers + 1 embedding worker, or N embedding workers behind a load balancer. The API client interface for the embedding service is documented in `cama/api/embedding_client.py` so the implementation can be swapped without changing API endpoints.

This split is named explicitly so operators with high concurrency expectations know the right scaling path.

---

## 8. SDK design

Two SDKs, both client libraries:

**Python first**, package name `cama-sdk` on PyPI (distinct from `cama` which is the server). Released independently — `cama-sdk 1.x` speaks `/v1/`.

**Node/TS second**, package `@lorienslibrary/cama-sdk`. Same shape, idiomatic TypeScript types.

The Python SDK target shape (concrete spec, lands in the next session):

```python
from cama_sdk import CAMA, Provenance, Affect

client = CAMA(api_key="cama_sk_live_...", endpoint="https://cama.example.com")

# Store a teaching from the user
mem = client.memories.create(
    text="prefer concise summaries with citations",
    memory_type="teaching",
    provenance=Provenance.teaching(by="user"),
    affect=Affect(valence=0.2, arousal=0.1, emotions={"trust": 0.6}),
)

# Search with blending + counterweights (on by default)
results = client.search("what does the user prefer", limit=10)
for r in results:
    print(f"[{r.score:.2f}] {r.memory_type}: {r.text[:80]}")

# Warm-boot a new thread
boot = client.threads.start(user_message="hey")
print(boot.journal_excerpt)
```

**12 lines.** With the tutorial wrapping 20 lines around it (auth, counterweight demo, delete-on-request), the "20 lines of code" pitch is real and runnable.

The SDK is *not* implemented in v1 of this PR. It's specified here so the API shape stays SDK-friendly.

---

## 9. Versioning policy

- **Path version `/v1/`.** Breaking changes ship at `/v2/`. Six-month minimum overlap before deprecating `/v1/`.
- **Within `/v1/`,** additive-only changes: new endpoints, new optional fields, new enum values (additive enums must include a documented graceful-handling rule for older SDKs).
- **Deprecation headers** on responses for any endpoint slated for removal:
  ```
  Deprecation: true
  Sunset: Wed, 21 May 2027 00:00:00 GMT
  Link: <https://docs.lorienslibrary.com/api/v2/migration>; rel="deprecation"
  ```
- **The SDK** is versioned independently (semver). Major bumps when the API version it supports changes; otherwise minor/patch as usual.

---

## 10. Observability

- **`api_audit_log` table** in the CAMA SQLite store. One row per request. Schema: `(id INTEGER PK, ts TEXT, key_fingerprint TEXT, dyad_id TEXT, endpoint TEXT, http_method TEXT, status_code INT, latency_ms REAL, request_body_hash TEXT, error_code TEXT)`. Append-only. The `request_body_hash` is SHA-256 of the canonical JSON for replay-detection only — the body itself is never logged.
- **No PII in logs.** Memory text, query text, consent values: never logged.
- **Prometheus metrics** at `GET /v1/metrics` (ops-key-gated): request rate, p50/p95/p99 latency per endpoint, error rate by `cama.violated_contract`, dyad-scoped throughput.
- **Structured stderr logs** in JSON format. Existing `~/.cama/logs/` continues to receive sleep-cycle / boot logs.

---

## 11. Deployment

```bash
pip install "cama[api]"
export CAMA_DB_PATH=/var/lib/cama/memory.db
export CAMA_API_KEY_DB=/var/lib/cama/keys.db
export CAMA_ALLOWED_ORIGINS=https://app.example.com
cama-api-server --host 0.0.0.0 --port 8080
```

Or via Docker:

```bash
docker run -p 8080:8080 \
  -v /var/lib/cama:/data \
  -e CAMA_ALLOWED_ORIGINS=https://app.example.com \
  ghcr.io/lorienslibrary/cama-api:1.26.0
```

The Docker image and `cama-api-server` console script land in a follow-up PR after v1 of this design is verified — both are designed-for, not built-in-this-PR.

Required env vars (no defaults that leak privilege):

| Var | Required | Purpose |
|---|---|---|
| `CAMA_DB_PATH` | yes | path to the SQLite memory store |
| `CAMA_API_KEY_DB` | yes | path to the keys + audit-log SQLite store (separate DB) |
| `CAMA_ALLOWED_ORIGINS` | yes | comma-separated allowlist for Origin checks |
| `CAMA_EMBEDDING_PROVIDER` | no | `local` (default) or `api` |
| `CAMA_RATE_LIMIT_DEV` | no | override default 60/min for dev keys |
| `CAMA_RATE_LIMIT_LIVE` | no | override default 600/min for live keys |

---

## 12. Compliance touch-points

| Regulation | What v1 provides | What's needed for compliant deployment |
|---|---|---|
| **HIPAA** (PHI in health-tech use) | Audit log shape, real delete, dyad isolation | Operator must add encryption at rest, BAA-eligible hosting, retention policy |
| **GDPR** (EU users) | `GET /v1/dyads/{id}/export` for portability, `DELETE /v1/dyads/{id}` for erasure | Operator publishes a privacy policy that names the lawful basis |
| **COPPA** (under-13 users) | Consent flow primitives | **v1 is NOT approved for under-13 deployments.** v2 will add age-gating + parental-consent middleware before this is unblocked |

The API does not claim compliance itself — it provides the primitives a compliant operator builds with.

---

## 13. What's in scope for the v1 PR landing this surface

| Item | In this PR | Follow-up |
|---|---|---|
| `cama/api/server.py` (FastAPI app, lifespan) | ✅ | — |
| `cama/api/auth.py` (Argon2 key validation, dyad scoping middleware) | ✅ | — |
| `cama/api/schemas.py` (Pydantic models, enums) | ✅ | — |
| `cama/api/errors.py` (RFC 7807 envelope) | ✅ | — |
| `POST /v1/memories` | ✅ | — |
| `GET /v1/memories/{id}` | ✅ | — |
| `POST /v1/search` (with counterweight injection wired in) | ✅ | — |
| `POST /v1/thread/start` | ✅ | — |
| `GET /v1/health` | ✅ | — |
| `tests/test_api.py` (auth, provenance, dyad scope, counterweight) | ✅ | — |
| `[api]` extra in `pyproject.toml` + console script | ✅ | — |
| `THREAT_MODEL.md` | ✅ | — |
| API.md (this file) linked from README + EVIDENCE | ✅ | — |
| `cama-sdk` package on PyPI | ❌ | next session |
| Tutorial / "20 lines" example repo | ❌ | next session |
| Webhooks (`POST /v1/webhooks`) | ❌ | v1.1 |
| Multi-tenant tenant-key issuance via API | ❌ | v1.1 (today: via `cama-ops keys create`) |
| Ops CLI (`cama-ops`) | ❌ | v1.1 |
| Embedding worker separation | ❌ | v1.1 |
| Docker image on GHCR | ❌ | follow-up |
| Hosted SaaS | ❌ | separate company conversation |

What's in this PR is enough to demonstrate the architecture and let a developer integrate against a self-hosted CAMA. What's deferred is named, scoped, and recoverable.
