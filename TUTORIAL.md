# CAMA in 20 Lines of Code

The pitch: your AI app forgets everything between sessions. Bolting on RAG gives you *semantic recall* but not *continuity*, no provenance, no consent state, no anti-spiral protection, no warm boot. CAMA gives you all of those in one layer.

This tutorial shows what integrating CAMA looks like for a consuming application. The runnable code below is **22 lines**, exercising every architectural primitive: typed memory writes with provenance, blended search with automatic counterweight injection on negative affect, warm-boot of a new conversation thread, and real (right-to-be-forgotten) delete.

---

## Prerequisites

```bash
pip install "cama[api]"            # the server + SDK
export CAMA_DB_PATH=~/.cama/memory.db
export CAMA_API_KEY_DB=~/.cama/api_keys.db
cama-api-server --host 127.0.0.1 --port 8080 &

# Mint a key bound to the default dyad. The plaintext is shown once.
python -c "from cama.api.auth import create_key; print(create_key(dyad_id='default', kind='live')[0])"
# cama_sk_live_<paste-this-into-your-app>
```

That's the one-time setup. From here on, the application code is what matters.

---

## The 22-line example

```python
from cama.sdk import CAMA, Provenance, Affect

# 1. Connect (replace with your real key + endpoint)
client = CAMA(
    api_key="cama_sk_live_REPLACE_ME",
    endpoint="http://127.0.0.1:8080",
)

# 2. Store a user-authored teaching: provenance is enforced at the boundary
mem = client.memories.create(
    text="the user prefers concise summaries with citations",
    memory_type="teaching",
    provenance=Provenance.teaching(by="user"),
    affect=Affect(valence=0.2, emotions={"trust": 0.6}),
)
print(f"Stored memory #{mem.id}, status = {mem.status}")

# 3. Search with blended retrieval. Anti-spiral counterweight injection
#    fires automatically when the query affect is strongly negative.
results = client.search(
    "what does the user prefer",
    affect=Affect(valence=-0.7, emotions={"grief": 0.8}),
)
print(f"{len(results)} results; "
      f"{results.counterweights_injected} counterweights injected")

# 4. Warm-boot a new session
boot = client.threads.start(user_message="morning, where did we leave off")
print(boot.journal_excerpt)
```

That's all of it. Twenty-two lines total, including blank lines and the import. Every architectural primitive is exercised:

| Line | What happens architecturally |
|---|---|
| `Provenance.teaching(by="user")` | Enforces NOT NULL provenance, the API would 422 if you tried to skip it |
| `Affect(valence=-0.7, emotions={"grief": 0.8})` | Triggers counterweight injection on the search, the result set is automatically supplemented with non-negative resonant memories |
| `Provenance.inference(by="assistant")` (try it) | Would force `status=provisional` and require a user-authored consent token before promotion |
| `client.threads.start(...)` | Returns the dense identity payload that the assistant should read before composing its first response |

---

## What this *doesn't* show, but the contract guarantees

* **No dyad spill.** Every read and write is scoped by the bearer token's dyad ID. A second app using a different key cannot see your dyad's memories. Cross-dyad reads return 404 (not 403), so existence is not leaked.
* **Real delete.** `client.memories.delete(mem.id)` is permanent. The row is gone; associated affect, embeddings, and librarian-membership rows cascade-delete. No soft-delete tombstone.
* **Inference safety.** Try changing the provenance to `Provenance.inference(by="assistant")` and observe `mem.status == "provisional"` in the response. The SDK ships the architectural guarantee that "AI cannot self-promote its own teachings", the row stays provisional until a user-authored consent flow promotes it.
* **Audit log.** Every API call writes a row to `api_audit_log` with the key fingerprint, dyad ID, endpoint, status code, latency, and a SHA-256 hash of the request body (the body itself is never logged, privacy posture). Operators can detect abnormal usage by key.

---

## Handling errors

The SDK maps the server's RFC 7807 problem-details responses to typed exceptions. You can branch on the violated contract rather than parse prose:

```python
from cama.sdk import (
    CAMA, Provenance,
    CamaProvenanceError, CamaDyadScopeError, CamaRateLimitError,
)

try:
    mem = client.memories.create(
        text="x",
        memory_type="teaching",
        provenance=Provenance.teaching(by="user"),
    )
except CamaProvenanceError as e:
    # Missing proposed_by, source_type, or unknown memory_type
    print(f"Fix: {e.fix}")
except CamaDyadScopeError:
    # Tried to read or modify a resource outside this key's dyad
    pass
except CamaRateLimitError:
    # 60 req/min for dev keys, 600 req/min for live keys
    pass
```

The full exception list is in `cama.sdk`, every value in the closed `cama.violated_contract` set has a matching exception class. See [API.md § 5](API.md) for the canonical set.

---

## What CAMA gives you that off-the-shelf memory does not

| | Generic RAG / vector store | CAMA |
|---|---|---|
| Semantic recall | ✅ | ✅ |
| Provenance (who/why/when said this) | ❌ (or manual) | ✅ enforced at API boundary |
| Inference vs teaching distinction | ❌ | ✅ provisional-by-default for AI inferences |
| Anti-spiral protection on negative-affect queries | ❌ | ✅ automatic counterweight injection |
| Warm boot (compressed identity payload) | ❌ | ✅ `client.threads.start()` |
| Right-to-delete with audit manifest | ❌ (typical RAG) | ✅ enforceable |
| Dyad-scoped isolation between users | usually multi-tenant glue | ✅ architectural; cross-dyad reads return 404 |

The pitch isn't "we have memory", every system has memory. The pitch is "we have memory with the safety primitives built in, enforced at the boundary, so the consuming app can't accidentally drop them by forgetting a flag."

---

## Going further

* **Architecture**: see [ARCHITECTURE.md](ARCHITECTURE.md) for the storage model, three-layer memory architecture, and the librarian routing system.
* **Retrieval algorithm details**: see [RETRIEVAL.md](RETRIEVAL.md) for the four-signal blended scoring and the Phase 1 → 2.6 routing evolution.
* **API surface**: see [API.md](API.md) for the full endpoint reference, error envelope, auth model, and versioning policy.
* **Threat model**: see [THREAT_MODEL.md](THREAT_MODEL.md) for the 18-row attack-and-mitigation matrix.
* **Empirical claims with proof**: see [EVIDENCE.md](EVIDENCE.md) for the claim/proof/limitation matrix across the portfolio.
