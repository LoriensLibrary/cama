# ARCHITECTURE: CAMA Media Memory

This document defines the module's **boundaries**, its internal layers, and the
split between what CAMA owns and what external services provide. For the concrete
entities and relationships, see DATA_MODEL.md.

## 1. Guiding principle

**CAMA remembers; perception describes.**

Remembering is hard, durable, and safety-critical: storage, provenance, the
provisional-to-confirmed lifecycle, emotional indexing, associative and temporal
retrieval, correction, and forgetting. That all lives in the existing CAMA core.

Describing is replaceable and stateless: reading EXIF, hashing bytes, making a
thumbnail, asking a vision model what is in a frame. That is the only genuinely
new capability, and it lives behind adapters that produce structured
**observations**. The memory core never imports a vision model.

## 2. Boundaries (what is in vs out of this module)

**In scope**

- A perception layer (adapters) that turns a media file into observations.
- A mapping layer that writes observations into CAMA memory as provisional
  inferences, and translates user corrections into teachings.
- Media-specific entities and edges (MediaAsset, Event, Trip, Post, Engagement)
  layered on CAMA's existing people/place/memory graph.
- A query planner that expresses natural-language media questions in terms of
  the existing retrieval layer (see RETRIEVAL.md).
- Posting-state tracking and a calendar suggestion service.

**Out of scope (reused from CAMA, not rebuilt here)**

- The memory store and its schema primitives (cama.core, cama.memory).
- Provenance types and the provisional-to-confirmed lifecycle.
- Emotional indexing (valence, arousal, emotion chord).
- Associative retrieval and blended scoring (see RETRIEVAL.md).
- Temporal recall / "on this day" (cama.temporal).
- Consent, sensitivity levels, deletion, and threat model (DATA_HANDLING.md,
  THREAT_MODEL.md, SECURITY.md).

**Explicitly excluded from the module entirely (for now)**

- Automatic facial identification of unknown people (see PRIVACY_SAFETY.md).
- Publishing integrations that post on the user's behalf (export only until the
  review workflow is trusted).

## 3. Proposed package layout

```
cama/media/
  __init__.py
  docs/                 # this design set
  perception/           # ADAPTERS: file -> observations (stateless)
    exif.py             #   capture time, GPS, camera (Pillow / piexif)
    hashing.py          #   sha256 (identity) + perceptual hash (near-dup)
    thumbnails.py       #   still + video keyframe thumbnails (Pillow / ffmpeg)
    vision_local.py     #   local scene/object/caption tags (CLIP / BLIP), opt-in
    ocr.py              #   text in screenshots/documents (later)
    scenes.py           #   video scene segmentation (later)
  memory/               # MAPPING: observations <-> CAMA memories
    assets.py           #   MediaAsset records + status lifecycle
    mapping.py          #   observation -> provisional inference; correction -> teaching
    entities.py         #   event / trip / theme edges over cama people/places
    posts.py            #   post + publication + engagement
  retrieval/            # QUERY: natural language -> existing retrieval layer
    query.py
  planning/             # calendar suggestion + export (no auto-publish)
    calendar.py
    export.py
  cli.py                # thin operator entry points for early milestones
```

Names are proposals. Codex should confirm the real seams in `cama.core`,
`cama.memory`, `cama.ingest`, and `cama.temporal` before wiring anything (see
DECISIONS.md, ADR-0002).

## 4. What CAMA owns vs what external services provide

| Concern | Owner | Notes |
| --- | --- | --- |
| Memory store and schema | **CAMA core** | Reuse cama.core / cama.memory. Do not create a parallel database. |
| Provenance + provisional/confirmed lifecycle | **CAMA core** | Observation = inference; correction = teaching. |
| Emotional index (valence/arousal/chord) | **CAMA core** | A photo's tone is stored the same way an exchange's tone is. |
| Associative + temporal retrieval | **CAMA core** | Query planner targets the existing retrieval layer. |
| Consent, sensitivity, deletion | **CAMA core** | Media adds a `minor` sensitivity flag on top. |
| Binary media (originals, thumbnails) | **Module, local disk** | Content-addressed store keyed by sha256. Never store pixels in the memory DB. |
| EXIF / hashing / thumbnails | **Module adapters (local)** | Deterministic, offline, no external calls. |
| Vision tags / captions | **Local model by default** | CLIP/BLIP-class. Runs on device. |
| Cloud vision | **Opt-in, off by default** | Triggers biometric-law duties; never for minor-flagged assets. See PRIVACY_SAFETY.md. |
| Caption/story writing | **External assistant (ChatGPT)** | Fed structured memory, not raw images. |
| Publishing | **Deferred** | Export drafts; the human posts (e.g., via Meta Business Suite). |

## 5. Data flow (import to recall to post)

1. **Import.** Point the module at a folder. For each file: compute sha256
   (identity), copy/keep the original in the content-addressed store, read EXIF,
   generate a thumbnail. Write one `MediaAsset` record. Re-running is idempotent
   (sha256 dedup).
2. **Observe.** Perception adapters produce observations (scene, objects,
   animals, landmarks, a draft caption, aesthetic/mood cues). Each is written as
   a **provisional inference** with model, version, and confidence.
3. **Review.** The user confirms, edits, or rejects observations. Confirmations
   and manual labels become **teachings** that outrank any model output.
4. **Connect.** Assets are grouped into events and trips (time + place), linked
   to people/pets (explicit labels), themes, and emotional tone. One asset can
   belong to many contexts via edges, never by duplication.
5. **Recall.** Natural-language queries are planned against the existing
   retrieval layer, blending semantic, associative, temporal, and emotional
   scoring.
6. **Plan and close the loop.** The calendar service suggests 1-2 posts/day from
   unused, high-fit memories. Selected assets export with caption drafts. Once
   posted, the post, platform, caption, hashtags, and later the engagement
   outcome are written back into memory, so the library learns about itself.

## 6. Non-negotiable invariants

- Originals are never mutated and never leave the device by default.
- No pixels in the memory store; the store holds records, derived text, and edges.
- Every AI-derived fact is provisional and carries its provenance and confidence.
- User corrections always win over model output.
- Deletion is real and cascades to derived memories and edges (see PRIVACY_SAFETY.md).
