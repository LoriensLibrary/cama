# DECISIONS: CAMA Media Memory

Architectural Decision Records. Each has a status: `proposed`, `accepted`,
`superseded`. Proposed records are Claude's recommendation pending confirmation
against the existing codebase or a product-owner call.

---

## ADR-0001 - Media Memory is a native CAMA module, not a separate app
**Status:** accepted (product owner directive).
**Decision.** Build media memory inside the `cama` repo as `cama/media/`, reusing
the existing memory core, retrieval, and temporal layers.
**Why.** The value is persistent, provenance-aware, emotionally-indexed memory,
which CAMA already provides. A separate planner would duplicate all of it and
prove nothing about the thesis.

## ADR-0002 - Reuse the CAMA store; do not create a parallel database
**Status:** proposed.
**Decision.** MediaAsset, Observation, Event, Trip, Post, and Engagement persist
through the existing CAMA storage layer (cama.core / cama.memory), not a new
standalone database.
**Open.** Codex to confirm the concrete extension point (schema/migration path,
multi-tenant boundary per MULTI_TENANT.md) before implementing persistence.
Develop against a thin storage interface until confirmed.

## ADR-0003 - Observations are provisional inferences; corrections are teachings
**Status:** accepted.
**Decision.** Every machine-derived claim is stored as a provisional inference
with model, version, and confidence. User confirmation or manual labeling emits
a durable teaching that outranks model output. Rejection zeroes the weight.
**Why.** This is CAMA's provenance model applied unchanged to media, and it is
what makes AI tagging safe to trust and correct.

## ADR-0004 - Local-first perception; cloud vision is opt-in and off by default
**Status:** accepted.
**Decision.** EXIF, hashing, thumbnails, and vision tagging run on device. Cloud
vision is a per-batch opt-in, never the default, and never for assets flagged
`is_minor` or high sensitivity.
**Why.** Sending identifiable people (especially children) to cloud vision
triggers biometric-law duties (notice, consent, deletion) that fall on the
operator. Local processing avoids them entirely. See PRIVACY_SAFETY.md.

## ADR-0005 - No automatic facial identification of unknown people
**Status:** accepted (product owner directive).
**Decision.** The module may detect that a face region exists, but it will not
identify or cluster unknown people automatically. People and pets are named only
by explicit user labeling.
**Why.** Identity is consequential and easy to get wrong; it must stay a
human decision.

## ADR-0006 - Binary media never enters the memory store
**Status:** accepted.
**Decision.** Originals and thumbnails live in a content-addressed store on local
disk, keyed by sha256. The memory store holds records, derived text, edges, and
paths, never pixels.
**Why.** Keeps the memory store small, portable, and fast; keeps originals
intact and independently backup-able.

## ADR-0007 - Publishing is deferred; MVP exports drafts only
**Status:** accepted.
**Decision.** No integration that posts on the user's behalf until the memory and
review workflow is dependable. The MVP exports selected assets with caption
drafts; the human publishes (for example via Meta Business Suite).
**Why.** A homegrown auto-poster risks platform-policy trouble and posts before
the library is trustworthy. Trust first, automation later.

## ADR-0008 - Track AI-generated media for platform disclosure
**Status:** accepted.
**Decision.** MediaAsset carries an `ai_generated` flag so exported/published
AI-generated or AI-edited images can be disclosed per platform rules (e.g.,
Meta's AI-content labeling).
**Why.** Disclosure of AI-generated imagery is a platform requirement; the data
model should make it trivial to honor.
