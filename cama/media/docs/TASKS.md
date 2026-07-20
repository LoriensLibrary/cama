# TASKS: CAMA Media Memory

Prioritized backlog. Status: `todo`, `in-progress`, `blocked`, `done`. Codex can
start TASK-001 immediately; everything below it is sequenced but not yet
fully specified.

---

## TASK-001 - Ingest a folder into MediaAsset records  (todo, ready)

**The smallest first implementation task.** No vision, no UI, no network.

**Goal.** Point the module at a local folder and produce durable, deduplicated
MediaAsset records with preserved originals and thumbnails.

**Scope**
1. Create the `cama/media/` package skeleton per ARCHITECTURE.md section 3
   (perception/, memory/, retrieval/, planning/, cli.py). Empty modules are fine.
2. Implement `perception/hashing.py`: sha256 of file bytes; leave a `phash`
   stub returning None for now.
3. Implement `perception/exif.py`: extract `captured_at`, `gps_lat/lng`,
   `camera_make/model` from images (Pillow). Gracefully handle missing EXIF.
4. Implement `perception/thumbnails.py`: write a bounded-size thumbnail.
5. Implement `memory/assets.py`: a `MediaAsset` record and an idempotent
   `ingest_folder(path)` that walks images, computes sha256, copies originals
   into a content-addressed store, generates thumbnails, and upserts one row per
   unique sha256. Re-running adds zero duplicates.
6. Provide `cli.py` entry point: `python -m cama.media.cli ingest <folder>`
   printing a summary (seen / new / duplicate) and writing an Import row.
7. Tests in `tests/` using a handful of small sample images, including one
   duplicate and one file with no EXIF.

**Storage decision (do this first).** Confirm where MediaAsset rows should live:
reuse the existing CAMA store (cama.core / cama.memory) or a module-local table
registered with it. Record the answer in DECISIONS.md (ADR-0002) before coding
the persistence path. Until confirmed, develop against a thin storage interface
so the backend can be swapped.

**Acceptance criteria**
- `ingest_folder` on a 10-image folder creates 10 assets; a second run creates 0.
- A duplicate file (same bytes, different name) yields 1 asset, not 2.
- Assets without EXIF still import (captured_at = null), no crash.
- Every asset has a preserved original path and a thumbnail path.
- Tests pass; no network calls; originals are never modified.

**Out of scope.** Vision tagging, events/trips, people, posts, queries, any UI.

---

## Backlog (sequenced, specify when reached)

- **TASK-002** Perceptual hash + near-duplicate grouping (fill the `phash` stub).
- **TASK-003** Local vision adapter: scene/object/animal tags + draft caption as
  provisional observations (`perception/vision_local.py`, `memory/mapping.py`).
- **TASK-004** Review flow: confirm / edit / reject observations; confirmations
  become teachings (provenance lifecycle).
- **TASK-005** Event grouping by time + place; Trip grouping over events.
- **TASK-006** Explicit people/pet labeling and asset-person edges.
- **TASK-007** Emotional tone + narrative theme edges.
- **TASK-008** Natural-language query planner over the retrieval layer
  (start with "what have I never posted?" and trip-scoped unused queries).
- **TASK-009** Posting-state model: unused / draft / scheduled / posted.
- **TASK-010** Calendar suggestion (1-2/day, theme-balanced) + export with caption drafts.
- **TASK-011** Write posted result and engagement outcome back into memory.
- **TASK-012** Deletion cascade + export-time GPS redaction (see PRIVACY_SAFETY.md).

## Working agreement

- Keep PRs small and mapped to one task.
- Update DECISIONS.md whenever a choice is made that a future contributor would
  otherwise have to reverse-engineer.
- Do not add publishing integrations or automatic facial identification without
  an explicit decision recorded here and in PRIVACY_SAFETY.md.
