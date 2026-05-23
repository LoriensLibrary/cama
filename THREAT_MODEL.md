# CAMA Threat Model

Companion to [`API.md`](API.md) and [`SECURITY.md`](SECURITY.md). This document names the attacks the CAMA architecture and API are designed against, and the chosen mitigation for each.

The format is deliberately a flat table rather than a STRIDE-style narrative — a reviewer can scan, find the attack they care about, and check the mitigation. Adversarial-examples tests for several rows live in `tests/test_api.py` (the rows marked **✅ test**).

This document is also the answer to a question the external code review correctly raised: *"What attacks did you actually consider?"*

---

## Scope

| In scope | Out of scope |
|---|---|
| The HTTP API surface defined in `API.md` v1 | Cryptanalysis of TLS itself |
| The SQLite persistence layer | Physical security of the host machine |
| The dyad-isolation contract from `MULTI_TENANT.md` | Vulnerabilities in dependencies we don't ship (Python, FastAPI, sqlite3) |
| The MCP server `cama_mcp.py` | Side-channel attacks on the host CPU |
| The published Python SDK (when shipped) | Social engineering of the operator |
| Stored memories and retrieval results | Quantum attacks on Argon2id |

Out-of-scope items are not "ignored." They're acknowledged dependencies on the operator (TLS termination, host hardening, dependency patching) or the broader ecosystem.

---

## Attack-and-mitigation matrix

| # | Threat | Vector | Mitigation | Status |
|---|---|---|---|---|
| **1** | **Stolen live API key** is replayed by an attacker | Compromise of operator's secrets store, accidental commit to a public repo, log exfiltration | (a) Keys hashed at rest with Argon2id. (b) Per-key fingerprint in audit log lets an operator detect abnormal usage. (c) `cama-ops keys revoke <fingerprint>` cuts off a key in O(seconds). (d) GitHub secret-scanning + push-protection are enabled on the public repo to prevent committed keys (see today's PR #8 on the cama repo). | **✅ test** + design |
| **2** | **Tenant key attempts cross-dyad read** (read a dyad it didn't create) | Malicious tenant operator, compromise of a tenant key with elevated assumptions | API enforces `key.dyad_scope ⊇ request.dyad_id` at the bearer-validation middleware *before* any handler runs. Cross-dyad attempts return 404 (not 403 — does not leak existence). Same scope check repeated at the SQL layer: every query is parameterized with the auth'd dyad ID; there is no SELECT path without that filter. | **✅ test** + design |
| **3** | **Prompt injection in stored memory text** that is later retrieved and read by an assistant | A malicious user posts a memory whose text instructs the downstream assistant to ignore prior context, exfiltrate, etc. | The API does *not* sanitize stored content (sanitization is a downstream responsibility). What the API *does* provide: (a) every retrieved memory carries its `proposed_by` and `source_type` fields, so a downstream assistant can refuse to follow instructions from memories with `proposed_by=user` if it chooses; (b) the canonical retrieved-memory envelope makes provenance unmistakable to the consuming model. This is documented in `API.md` §4.5. | **design** (deferred to consumer) |
| **4** | **Retrieval poisoning** — corrupt the counterweight pool by writing fake-positive memories | Attacker writes 1000s of memories tagged with positive-affect counterweight types, polluting the anti-spiral pool | (a) Writes are bearer-token gated. An attacker needs a valid key. (b) `counterweight_type` is an enum (closed set); arbitrary values rejected at the API boundary. (c) The default `consent_level` for assistant-proposed memories is `low` so they don't dominate retrieval. (d) Audit log captures every write — abnormal write rates are detectable. | **design** |
| **5** | **Replay** — captured request payload replayed later by attacker | Network capture, log spelunking | (a) HTTPS required in production (TLS handles wire-level replay). (b) `request_body_hash` in audit log allows the operator to detect duplicate-body bursts. (c) Sensitive endpoints (consent grant, delete) carry one-shot tokens or X-Confirm headers that don't replay correctly. | **design** |
| **6** | **DoS** via expensive search queries flooded across all keys | Coordinated attacker bot net | (a) Per-key token-bucket rate limit. (b) Per-IP rate limit at the reverse proxy (operator-configured; not API-layer concern but documented). (c) `payload_too_large` (64 KB cap on memory text and 8 KB on query text) bounds per-request cost. (d) `cama_check_self` / readout doesn't trigger embedding compute — the cheapest path is always available. | **design** |
| **7** | **Supply-chain attack on the SDK** — malicious pypi package shadowing | Typo-squat or compromised maintainer | (a) `cama-sdk` package will be published with PEP 740 attestations (Sigstore). (b) The package's GitHub release will sign artifacts. (c) The SDK is small and source-readable; the docs encourage `pip install --require-hashes`. | **design** (pre-SDK-ship) |
| **8** | **Consent forgery** — attacker tricks user's browser into changing dyad consent | Malicious site uses XSRF / clickjacking to flip safety flags | (a) Origin header allowlist (configured per deployment). (b) `X-Confirm` header required for destructive consent changes. (c) Consent-grant flow uses a one-shot HMAC-signed token bound to (dyad_id, memory_id, action, nonce, TTL). (d) The consent UI is served on a different origin than the API to defeat same-origin attacks. | **design** |
| **9** | **Token-leakage via timing oracle** during bearer validation | Attacker measures response time to guess valid prefixes | Argon2id verification has fixed cost regardless of input. The library used (`argon2-cffi`) does constant-time comparison on the hash output. | **design** |
| **10** | **Bypass of provenance contract via crafted JSON** | Attacker submits `proposed_by: null` or other variant types | Pydantic models enforce strict enum membership (`Literal["user", "assistant", "system"]`). null is rejected as type mismatch. Out-of-enum values return 422 with `cama.violated_contract: "enum_value_unknown"`. | **✅ test** |
| **11** | **Bypass of inference-promotion contract** — assistant promotes its own inference without consent | Application code crafts a `PATCH /v1/memories/{id}/confirm` with a forged token | The consent token is HMAC-signed with a server-side secret. Verification is in `cama/api/auth.py`. Forgery requires either the secret (out of scope — secrets-management problem) or a valid signed token (which only the genuine consent-grant flow produces). | **✅ test** |
| **12** | **Race condition on delete** — caller deletes a memory mid-search | DELETE during in-flight POST /v1/search | The SQL layer holds short read locks; the deletion is wrapped in `BEGIN IMMEDIATE TRANSACTION`. If the search's read window covers the delete, search returns the deleted memory's data from its frame but a subsequent re-search excludes it. No torn reads. | **design** |
| **13** | **Audit log tampering** by a compromised host | Attacker with shell access edits `api_audit_log` | **Current state (v1):** the audit log is append-only *at the API layer* (no `UPDATE` or `DELETE` paths exposed). At the SQL layer, a compromised host can still silently mutate `api_audit_log` rows — no tamper-evidence is in place today. **Operational mitigation available now:** ship audit rows to a write-only remote log sink (HIPAA-grade requirement). **Planned (v1.1, not yet implemented):** per-row hash chain `sha256(prev_hash \|\| row_canonical_json)` so any later tampering is detectable on full replay. Until v1.1 lands, treat host-compromise audit integrity as an operator-controlled property, not a CAMA-controlled one. | **partial** (API-layer only; SQL-layer tamper-evidence is future work) |
| **14** | **Search exposes memories the caller "shouldn't see"** within their own authorized dyad | All memories in a dyad are reachable to the dyad's authorized key — there's no intra-dyad ACL | This is by design. A dyad is a single trust boundary. If finer-grained access control is needed (e.g., per-end-user memories within a tenant's dyad), the consuming application is responsible for that segmentation. The API doesn't model intra-dyad ACLs. Documented in `API.md` §2 ¶3. | **design** (out of scope) |
| **15** | **Denial of secrets** — attacker observes Argon2id wall-clock to detect when a key is valid (which is fast) vs invalid (which is slow) | Argon2 verification is deliberately slow; invalid keys take the full work | Constant-time. The slow path runs even when the key prefix doesn't match a known fingerprint — the API computes a dummy Argon2 hash against a fixed "no key" string when no candidate is found, so wall-clock is independent of whether a key exists. | **design** |
| **16** | **Right-to-delete bypass** — caller deletes a dyad but data lives on in backups | An operator restores from a pre-deletion backup, reviving "deleted" data | The delete is a logical contract on the *primary store*. Backup retention is an operator policy. The deletion manifest includes a timestamp; the operator's documented policy must include "do not restore data deleted after backup time T unless explicitly authorized to undo the deletion." This is documented operationally, not enforced architecturally. | **design** (operational) |
| **17** | **Side-effects from search via tool-using assistants** — an assistant uses search results to take destructive actions | The API doesn't know what the downstream assistant will do | Search is read-only on memories. The API doesn't have a "delete via search match" path. Tool side effects are the assistant's responsibility. Document as a downstream concern. | **design** (out of scope) |
| **18** | **Persona leakage across dyads** via the hive layer | Patterns flow up to the hive — could they re-bind to identities? | The hive protocol (`cama/hive/`) strips per-memory affect signatures to (valence_bucket, arousal_bucket, dominant_emotion, topic_category, time_bucket) before publication. No per-row identifying content. k-anonymity over the bucket combinations is enforced before publish. See `MULTI_TENANT.md` and `cama/hive/cama_hive_protocol.py`. | **design** + isolation tests in `tests/test_hive_protocol.py` |

---

## What this document does NOT claim

- **Not all of these mitigations have adversarial tests.** Rows marked **✅ test** have tests in `tests/test_api.py` (or already in `tests/test_hive_protocol.py`). Rows marked **design** have the design and the SQL/middleware path verified but no explicit red-team test. Building the missing tests is itemized in the next-session backlog.
- **No formal verification.** This is a threat enumeration, not a proof. A hostile reviewer with time to spend might find a gap; the goal of this doc is to make the next gap easier to find and patch.
- **Not a complete security architecture.** TLS, host hardening, secrets management, backups, intrusion detection — all are operator concerns, named here only where they intersect a design decision.

---

## How to use this document

1. **Before deploying CAMA in a sensitive context**, read the table and confirm each row's mitigation matches your deployment's posture.
2. **If you find a threat not on this list**, file a `security/` issue on the cama repo or contact the maintainer per [`SECURITY.md`](SECURITY.md).
3. **If you implement a new endpoint or change an existing one**, update this document. The matrix is the contract — drift is a smell.
