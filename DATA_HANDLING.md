# CAMA: Data Handling

A plain-language description of what CAMA stores, who can see it, how long it persists, and what is explicitly excluded. This is a **posture document**, not a legal privacy policy, CAMA currently has one user (the author) and no external customers. The document exists for researchers evaluating CAMA's privacy architecture, future partners considering applied deployment, and anyone reading the source who wants to understand the actual data semantics before reading the schema.

If CAMA becomes a multi-user product in any vertical (education, healthcare, coaching, etc.), this document becomes a starting spine for the real privacy policy that vertical will require, but every commitment below would need to be re-evaluated against the specific regulatory context (FERPA / COPPA / HIPAA / MHMDA / BIPA / GDPR / etc.).

---

## What CAMA stores

CAMA stores memory records on disk as rows in a SQLite database (`~/.cama/memory.db` by default). The schema is documented in [README.md](README.md#architecture) and visible in [cama_mcp.py](cama_mcp.py)'s `_init(c)` function.

| Category | What it is | Schema location |
|---|---|---|
| **Teachings** | User-authored memory text + metadata. Durable, full weight, no expiry. | `memories` table where `source_type='teaching'` |
| **Inferences** | Assistant-generated hypotheses about the user. Provisional, time-limited, require explicit confirmation before promoting to durable. | `memories` table where `source_type='inference'` |
| **Affect annotations** | Per-memory valence / arousal / emotional chord. Recomputable from raw text. | `memory_affect` table |
| **Embeddings** | Semantic vector representation of each memory. Recomputable. | `memory_embeddings` table |
| **Relational edges** | Typed connections between memories (resonance, contradiction, elaboration, etc.). | `edges` table |
| **People / songs / islands** | Relational entities for organizing memory around recurring contexts. | `people`, `songs`, `islands`, `island_members` tables |
| **Identity self-model** | Small persistent key/value record of the AI instance's own self-state (drift signals, identity anchors). | `aelen_state` table |
| **Compliance metadata** | Per-session record of which protocol steps were completed (thread_start ran, exchanges stored, etc.). For auditability of the AI's own use of the memory system. | `session_compliance` table |
| **Daily context summaries** | Aggregate emotional / behavioral summaries per day, used for warm-boot continuity. Derived; never raw conversation text. | `daily_context` table |
| **Active ring** | Bounded working-memory buffer of recently-activated memories (default 30 slots, oldest overwritten). Operational state, not persistent history. | `ring` table |

---

## Provenance is enforced at the schema level

Every row in `memories` carries four columns that are **NOT NULL** and pinned by the pytest suite:

- `source_type`: `teaching` (user-authored) or `inference` (assistant-authored)
- `status`: `durable` / `provisional` / `expired` / `rejected`
- `proposed_by`: `user` / `assistant` / `system`
- `consent_level`: `low` / `medium` / `high` (per-memory granularity)

This separation is enforced in code, not just in policy. The system cannot promote its own inferences to durable status without explicit user confirmation; the schema rejects it.

The relevance to data handling: every memory has a traceable origin, an explicit confidence tier, and a consent annotation that downstream tools can branch on. This is the privacy architecture, not a UX layer on top of it.

---

## Who sees what

| Actor | Access | Mechanism |
|---|---|---|
| **The user** running CAMA on their own machine | Full read/write on their own database | Direct file access; MCP tools |
| **The AI instance** running with the user (e.g., Claude Desktop with CAMA MCP installed) | Read via tools, write via `cama_store_teaching` / `cama_store_inference` / `cama_store_exchange` only | MCP protocol; tool calls are visible to the user in the host application |
| **Other AI instances** (via the optional Hive coordination layer) | Restricted by per-token authorization. Hive messaging is opt-in and the destination AI never sees the source memory text, only summaries the sender explicitly shares. | `cama_hive_messages` + `cama_hive_messages_mcp` modules; `AELEN_TOKEN` shared-secret auth |
| **The public** | Aggregate statistics only, published as the [continuity-burden dataset](https://huggingface.co/datasets/LoriensLibrary/cama-continuity-burden) on HuggingFace. ~15 kB of JSON summaries derived from a private 66,380-message source corpus. No raw memories, no identifiable disclosures. | Static publication; not a live API |
| **Academic readers** | Published research papers describing the architecture, methodology, and aggregate findings. Individual memory records are never published. | 11 DOI-registered preprints on Zenodo, linked from the README |
| **Future external users** (if and when multi-user happens) | None today. Requires explicit consent flow + delete mechanism + audit log of access + a real privacy policy specific to the vertical. | Not yet built |

---

## How long things persist

| Category | Retention | Deletion path |
|---|---|---|
| **Teachings** (durable) | Indefinite, until user deletes | `cama_delete_memory` tool; or direct SQL on the local DB |
| **Inferences** (provisional) | 7 days default TTL. If not confirmed via `cama_confirm_memory`, transition to `expired` status. | Automatic via `cama_expire_stale`; never deleted, just marked expired |
| **Expired / Rejected** | Kept for audit, never deleted. Weight drops to 0; excluded from retrieval. | Manual via `cama_delete_memory` if needed |
| **Active ring** | 30 slots, FIFO overwrite. | Automatic |
| **Compliance metadata** | Per-session, indefinite | Manual cleanup; not user-facing |
| **The full database** | Indefinite, lives on the user's local machine | The user can delete `~/.cama/memory.db` at any time and lose nothing the system depends on except their own history |

There is no remote backup, no cloud sync, no telemetry. If the local file is deleted, the data is gone.

---

## What CAMA explicitly does NOT do

- **No telemetry or usage analytics.** The system makes no outbound calls except (optionally) to an embedding provider when computing semantic vectors, and (optionally) to the Hive HTTP API for cross-instance messaging if the user opts in. Neither path transmits memory content elsewhere by default.
- **No raw conversation corpus is published.** The aggregate continuity-burden dataset is ~15 kB of JSON summaries; it is not a replication corpus.
- **No third-party data sales.** There is no third party. The author is the sole data controller for their own local instance.
- **No automatic sharing between users.** The Hive coordination layer is opt-in per message, with explicit recipient targeting; there is no broadcast surface.
- **No PII-bearing evaluation files are tracked publicly.** Specific files known to reference real individuals by name in their evaluation fixtures (`cama_eval.py`, `cama_phase2_embed.py`, `cama_phase25_subcentroid.py`, `cama_check_self.py`, `label_drift_v3.py`, `dyad_specs.md`, `warm_validation_sample.csv`) are kept local-only via `.gitignore`. This is documented in [README.md](README.md#stability-and-reproducibility-boundary).

## Data at rest

- **The SQLite database at `~/.cama/memory.db` is not encrypted at rest by default.** This is acceptable for a single-user local deployment where the user controls the host machine. It is NOT acceptable for any deployment where the host machine is not under the user's sole control.
- **For multi-user / study deployments, full-disk encryption is required** (FileVault on macOS, BitLocker on Windows, LUKS on Linux). The threat model assumes that filesystem-level access equals data access, so OS-level encryption is the boundary that protects memories from a lost or compromised device.
- **A SQLCipher-backed option is on the roadmap** for deployments that cannot rely on the operator enabling full-disk encryption. Until then, the disk-encryption requirement is documented in the participant consent flow.
- **Personal calibration data lives outside the repo** in `~/.cama/user_aliases.json` and `~/.cama/identity_sentinels.json` (both gitignored). These files contain user-specific personalization for the affect-perturbation and identity-sentinel layers respectively; the shipped framework runs with empty defaults when neither is present.

---

## Current operating context (be specific)

- **N = 1.** CAMA currently has one user (the author, Angela Reinhold). All data in the production instance is the author's own.
- **Designer-as-participant.** The author is both the operator of the system and the subject of the memory records. This is honestly disclosed in the README and in the research papers; findings cannot be generalized without replication across users.
- **No external deployment.** There is no hosted CAMA service, no SaaS surface, no user accounts other than the author's local install. Anyone who clones the repo and runs CAMA does so against their own local database with their own memories.

---

## What would change at multi-user

If CAMA is applied in a real product (the Telos / Project-Companion / Haven verticals, or any future vertical), each of the following becomes non-optional:

- **Explicit, separable, opt-in consent** per memory category. Washington's My Health My Data Act and the EU GDPR Article 9 framework both require this for any health- or emotion-adjacent data, which CAMA's affect annotations qualify as.
- **Right to view, export, delete** every memory belonging to a user. The MCP tools already include `cama_delete_memory`, but a user-facing surface and audit trail would be added.
- **Audit log** of every memory access, who, when, which memory, via which tool. The `cama_supervisor` module is the structural starting point for this.
- **A real privacy policy** specific to the vertical (FERPA + COPPA for K-12, MHMDA + CCPA + HIPAA-adjacent for health coaching, etc.), reviewed by counsel.
- **Vendor agreements** (DPAs / BAAs) with subprocessors handling memory content, in particular any LLM provider used for embeddings or retrieval.

---

## Reporting / questions

This document is part of the public CAMA repo. Issues with the framing, missed cases, or specific scenarios you want clarified, open an [issue on the repo](https://github.com/LoriensLibrary/cama/issues) or contact [lorienslibrary@gmail.com](mailto:lorienslibrary@gmail.com).

If you are a researcher or potential partner evaluating CAMA for applied deployment, this document is a starting point, not the final word. The architectural choices documented here are real; the regulatory compliance work that turns them into a shippable product is yours (with our help) to build on top.

---

*Last reviewed: 2026-05-17. This document is reviewed when the schema changes, when a new actor type is added to the system, or when CAMA is applied in a new vertical.*
