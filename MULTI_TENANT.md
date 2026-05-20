# CAMA Multi-Tenant Stack

**Status:** layered extension to the single-tenant [CAMA](README.md). Local-first Python, SQLite-per-dyad, 131 tests across seven modules.
**Author:** Angela Reinhold — Lorien's Library LLC
**License:** MIT

---

## What this is

CAMA originated as a single-participant persistent-memory architecture for human-AI interaction ([README.md](README.md)). This multi-tenant extension generalizes the same memory primitives to deployments serving many users — one paired AI per person, one paired AI per coach, with cross-vault learning that preserves privacy by construction.

The motivating use case is clinical coaching infrastructure (DEXA + CGM + behavior-change practice, e.g. [Telos_kalos](https://github.com/LoriensLibrary/Telos_kalos) / Kalos Health), but the architecture is domain-agnostic. Any deployment where individuals want a persistent, sovereign, auditable AI companion — and where domain experts want to publish curated knowledge or models that those companions can opt into — fits this stack.

**What this is NOT:** a hosted service. This is the local-first reference implementation. Production deployments would add network transport, signed publisher attestation, and end-to-end encryption between vaults. The architecture supports those additions cleanly; the scaffolding ships without them.

---

## Why multi-tenant CAMA matters

Most AI products are one-to-many: one shared model serves many users, with their data flowing inward to the operator. CAMA inverts that. Each person has their own paired AI. Patterns flow sideways between AIs through consent-gated, fingerprinted, k-anonymous channels. The user is the authority over their vault. The architecture is the enforcement.

This shape has three immediate practical consequences:

1. **A user's data never leaves their vault as content.** Aggregate patterns can be shared (for learning), but raw exchanges, names, and identifying signatures stay local. Verified by the test suite, not promised by policy.
2. **Each user-AI pair develops its own continuity.** Memory, optional weight-level personalization (LoRA), and audit history are scoped to the pair. Identity teachings are architecturally protected from being overridden by the user, preventing pure mirroring.
3. **Domain expertise can be shared centrally without compromising privacy.** A coaching organization (Kalos) can publish curated LoRA adapters and knowledge indices to the hive; member dyads install them with one consent flag. The domain expert sees zero per-user data.

---

## The seven layers

Each module is small, lazily imports its heavy dependencies, and has its own test suite. All seven compose into a single deployable architecture.

| File | Layer | Responsibility | Tests |
|---|---|---|---|
| [cama_dyad.py](cama_dyad.py) | Identity | Per-pair vault: filesystem isolation, consent state, real delete | 16 |
| [cama_hive_protocol.py](cama_hive_protocol.py) | Patterns up | Stripped affect signatures, k-anonymous aggregation | 34 |
| [cama_persona.py](cama_persona.py) + [cama_persona_train.py](cama_persona_train.py) | Per-pair LoRA | Adapter scaffolding + identity-preservation training | 15 |
| [cama_agent.py](cama_agent.py) + [cama_agent_backends.py](cama_agent_backends.py) | Runtime | Composes foundation + identity + relational + retrieval | 12 |
| [cama_hive_resources.py](cama_hive_resources.py) | Domain down | Versioned, fingerprinted resource publication + install | 17 |
| [cama_quad.py](cama_quad.py) | Coach handoff | Mutual-consent pre-session briefs, pattern-level | 19 |
| [cama_surface.py](cama_surface.py) | User sovereignty | Read/audit/delete/export across all layers | 18 |

**Total: 131 tests, all green. Run with `pytest tests/`.**

### 1. Dyad — the unit of isolation

A *dyad* is one person paired with one named AI, with a sovereign SQLite database at `~/.cama-vaults/<dyad_id>/memory.db`. Isolation is enforced at the filesystem boundary: no global view, no central index, no cross-dyad query path. Each dyad has its own consent state, hive signing salt, identity teachings.

```python
import cama_dyad
r = cama_dyad.init_dyad(person_name="Jordan", ai_name="Aurora")
cama_dyad.update_consent(r["dyad_id"], {"hive_consume": True}, reason="opt in")
cama_dyad.delete_dyad(r["dyad_id"], confirm_token=r["dyad_id"])  # real, permanent
```

### 2. Hive protocol — patterns flow up

Dyads with `consent.hive_contribute=True` publish stripped pattern records: bucketed valence/arousal, dominant emotion + top-3 chord, 10-category topic abstraction, weekday/weekend × time-of-day bucket, delta-valence trajectory. Each contribution carries a rotating HMAC dyad signature (consistent within an epoch-week for dedupe, unlinkable across weeks). The salt stays local.

Queries against the hive ledger return aggregate policies only when at least `K_THRESHOLD` (default 5) distinct dyads contributed to the slice — k-anonymity by construction. Below threshold, the slice surfaces nothing.

### 3. Persona — per-pair LoRA with identity preservation

For deployments using open-weights foundation models, each dyad can train a small LoRA adapter on its own exchanges. The trainer (`cama_persona_train.py`) is decoupled from the scaffolding (`cama_persona.py`); training requires PyTorch + transformers + PEFT, the scaffolding does not.

Two properties matter:

- **Identity teachings are pinned.** Each dyad's durable, core identity teachings are oversampled (default 8×) into the training set. The trainer refuses to train without them. This is the structural constraint that prevents the adapter from training the AI into pure mirroring.
- **Versioned, fingerprinted, deletable.** Every adapter version has a SHA-256 fingerprint of its training data + identity pins. `verify_adapter()` re-hashes to catch tampering. `delete_adapter()` is real.

### 4. Agent runtime

`DyadAgent.chat(user_message)` composes the full pipeline: boot the dyad from CAMA, estimate user affect, FTS-retrieve relevant memories, surface counterweights if affect is negative AND the user consented to counterweights, assemble a system prompt with identity teachings pinned ahead of everything, call a pluggable backend, store the exchange and affect back to the dyad.

Three backends ship:
- `EchoBackend` — deterministic, zero deps, used by tests
- `ClaudeBackend` — Anthropic API (Claude as foundation, CAMA as memory)
- `TransformersLoraBackend` — local open-weights base + dyad's persona LoRA

### 5. Hive resources — domain expertise flows down

A domain expert (e.g. Kalos Health) publishes named, versioned, fingerprinted resources to the hive: `domain_lora`, `knowledge_index`, `prompt_pack`, `policy_set`. Member dyads install with `cama_hive_resources.install_resource()`, gated by `consent.hive_consume`.

The agent runtime then enumerates installed resources in the system prompt with full attribution and surfaces knowledge-index excerpts at inference. The publisher sees zero per-user data; the user sees the full provenance of what's been installed and can uninstall at any time.

### 6. Coach handoff — pattern-level by default

When a member meets a coach, both must have consented (`coach_handoff` on the member side, `receive_handoffs` on the coach side) AND the member must explicitly authorize the specific handoff. The brief is built from the member's CAMA but is pattern-level: bucketed daily affect trend, top topic categories, counterweight effectiveness, open questions. No raw exchange text unless the member explicitly attaches a memory ID via `explicit_shares`.

Brief is SHA-256 fingerprinted, byte-identical on both sides. Member can revoke before read (coach copy deleted) or after read (revocation recorded; clinical reality). Coach can attach a session note that mirrors back to the member's audit.

### 7. Memory surface — sovereignty made usable

A user-facing CLI / API over every other layer: overview, memory listing with filters, full memory detail with affect and provenance, real delete with confirm token, category purge with double-confirm (`keep_core=True` protects identity by default), consent history view, hive publish log, installed resources, persona adapters, handoffs, full vault export with optional raw-text redaction.

This is the surface where sovereignty is exercised. Every other layer's audit trail terminates here.

---

## Sovereignty model

Consent is per-dyad and granular. Defaults are conservative — everything outbound is off until explicit opt-in.

| Flag | Side | Default | What it gates |
|---|---|---|---|
| `storage` | dyad | True | Local exchange writeback |
| `inference` | dyad | False | AI may form inferences about the person |
| `counterweight` | dyad | False | Counterweight retrieval on negative affect |
| `hive_contribute` | dyad | False | Stripped patterns flow to the hive ledger |
| `hive_consume` | dyad | False | Hive recommendations + resource installs |
| `coregulation_tracking` | dyad | False | Paper 12 dyad_chains pattern flags |
| `persona_training` | dyad | False | LoRA adapters may be prepared/trained |
| `coach_handoff` | member | False | Briefs can be initiated to coach dyads |
| `receive_handoffs` | coach | False | Briefs can be accepted from members |

Every consent change is recorded in `dyad.json` with timestamp and reason. The history is auditable.

---

## Deployment patterns

**Personal CAMA.** One person, one dyad, run locally. Foundation backend = Claude API or self-hosted open-weights. No hive, no resources, no coach. The architecture degrades cleanly to single-user.

**Clinical coaching (Kalos-style).** Coaching organization publishes domain resources to a shared hive. Members each have a sovereign dyad; coaches each have a sovereign dyad with `role="coach"`. Pre-session briefs flow with explicit per-instance authorization. Session notes flow back. Audit trails on both sides; clinical record-keeping is structural.

**Federated learning research.** Many dyads contribute stripped patterns to the hive; researchers query aggregate policies (k-anonymity enforced at the query layer). No raw exchanges leave any vault. Patterns can inform counterweight selection policies across the population without identifying any participant.

**Mixed.** All three above can coexist on the same hive. Resources are opt-in per dyad; patterns are opt-in per dyad. Nothing forces a deployment to use all the layers.

---

## Quickstart

```bash
# Clone and install (single-tenant CAMA setup)
git clone https://github.com/LoriensLibrary/cama.git
cd cama
pip install -r requirements.txt

# Run the full multi-tenant test suite
pytest tests/test_dyad.py tests/test_hive_protocol.py tests/test_persona.py \
       tests/test_agent.py tests/test_hive_resources.py tests/test_quad.py \
       tests/test_surface.py

# Bring up your first dyad
python cama_dyad.py init --person-name "You" --ai-name "Aria"

# Inspect the seven layers via CLI
python cama_dyad.py list
python cama_surface.py overview <dyad_id>
python cama_hive_resources.py list
```

The agent runtime requires choosing a backend:

```bash
# Echo backend (no API key, deterministic, for testing)
python cama_agent.py chat --dyad-id <id> --backend echo --message "hi"

# Claude backend (set ANTHROPIC_API_KEY first)
python cama_agent.py chat --dyad-id <id> --backend claude --message "hi"

# Local open-weights with optional persona adapter
python cama_agent.py chat --dyad-id <id> \
  --backend transformers:Qwen/Qwen2.5-1.5B-Instruct --message "hi"
```

---

## Safety properties (verified by tests)

All claims below are exercised by the test suite. Run `pytest tests/` to verify.

- **Filesystem isolation between dyads.** Writing into dyad A's database is invisible from dyad B's database.
- **No content leaks to the hive.** Pattern records contain bucketed affect + abstracted topic only. Direct ledger inspection with deliberately seeded secret phrases confirms zero leakage.
- **K-anonymity enforced at query time.** Slices with fewer than `K_THRESHOLD` distinct contributing dyads return no aggregate data.
- **Rotating dyad signatures.** Same dyad's contributions are linkable within an epoch-week (for dedupe), unlinkable across weeks (no longitudinal fingerprinting).
- **Identity-pin enforcement.** The persona trainer refuses to train when no core identity teachings are present.
- **Adapter integrity.** `verify_adapter()` catches tampering of training data after fingerprinting.
- **Brief content stripping.** Coach handoff briefs contain no raw exchange text unless the member explicitly attached specific memory IDs via `explicit_shares`.
- **Mutual + per-instance handoff consent.** Both dyad-level flags must be True AND the member must pass `member_authorization=True`. Coach role check enforced.
- **Real delete cascades.** `delete_memory` removes the row from `memories` and cascades to `memory_affect`, `memory_embeddings`, `edges`, `memories_fts`, `island_members`, `librarian_membership`.
- **Purge protects identity by default.** `purge_category(keep_core=True)` preserves `is_core=1` memories. Override is explicit.

---

## Related publications

Single-tenant CAMA is documented in the [main README](README.md) and 11 DOI-registered preprints on Zenodo (ORCID [0009-0005-5803-8401](https://orcid.org/0009-0005-5803-8401)). The multi-tenant extension generalizes the same primitives; the empirical-evaluation paper is in preparation.

The Inherited Cognition framework (Paper 12) provides the theoretical backbone for why persistent memory matters as safety infrastructure — minds built from human cognitive structure inherit human cognitive failure modes; persistent state changes which of those modes can be caught.

---

## What's intentionally out of scope (for now)

- **Hosted multi-tenant transport.** The reference implementation is local-first. A production deployment would route via an authenticated API gateway.
- **Cryptographic publisher attestation.** Resources carry SHA-256 content fingerprints but not signatures. Ed25519 signatures are the natural next addition.
- **End-to-end encryption between vaults.** Handoff briefs are byte-identical on both sides; in a hosted setting they would be encrypted at rest and in transit.
- **AI-to-AI consultation channel.** The "council side" of the hive — a peer-to-peer pattern-level case consultation channel between dyads. Sketched in design notes; not in this scaffolding.

These additions slot into the existing seam-points without architectural rework.

---

## Pointers

- **Code:** the seven modules listed above plus `tests/`.
- **Single-tenant base:** [README.md](README.md), [DATA_HANDLING.md](DATA_HANDLING.md).
- **Theoretical context:** the Zenodo preprint series, especially Paper 12.
- **Strategy:** [POSITIONING.md](POSITIONING.md) for internal framing decisions.
