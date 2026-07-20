# ROADMAP: CAMA Media Memory

Milestones are vertical slices. We only fully specify the next one. Each
milestone maps to the product owner's 10-step MVP proposal and stays private and
local-first throughout.

## M0 - Foundation (this scaffold)

VISION, ARCHITECTURE, DATA_MODEL, ROADMAP, TASKS, DECISIONS, PRIVACY_SAFETY.
The map every AI and human on the project builds from.

## M1 - Ingest and Preserve  (MVP steps 1-2)

Import a folder of images. Preserve originals in a content-addressed store.
Record MediaAsset rows with sha256, EXIF (capture time, GPS, camera), and a
thumbnail. Exact-duplicate detection via sha256. Idempotent re-runs.

- Exit criteria: point at a folder twice; second run adds zero duplicates; every
  asset has a preserved original, a thumbnail, and captured_at when EXIF allows.

## M2 - Observe and Enrich  (MVP steps 3-4)

Local vision produces provisional observations (scene, objects, animals,
landmarks, a draft caption, mood cues). A review flow lets the user confirm,
edit, or reject. Confirmations become teachings.

- Exit criteria: a reviewed asset has at least one confirmed, provenance-tagged
  description; rejecting an observation zeroes its weight and it does not resurface.

## M3 - Connect  (MVP step 5)

Group assets into events and trips by time and place. Attach explicit
people/pet labels, themes, and emotional tone. Link related and near-duplicate
assets. One asset, many contexts, no duplication.

- Exit criteria: a day of photos collapses into a small number of events; an
  asset can be reached from a person, a place, a trip, and a theme.

## M4 - Recall  (MVP steps 6-7)

Natural-language query over the graph via the existing retrieval layer. Track
posting state (unused, draft, scheduled, posted) per asset and post.

- Exit criteria: "what have I never posted?" and "unused photos from our Boca
  Grande trip" return correct sets; posting state is never lost.

## M5 - Plan and Close the Loop  (MVP steps 8-10)

Suggest a 1-2 post/day calendar from unused, high-fit memories. Export selected
assets with caption drafts. Store the final post (platform, caption, hashtags)
and later its engagement outcome back into memory.

- Exit criteria: a week of suggestions balanced across themes; export produces
  assets plus a draft; a posted item is remembered as posted with its outcome.

## Longer-term direction (post-MVP, not scheduled)

- Duplicate and near-duplicate detection at scale (perceptual hashing).
- People and pet recognition with explicit user labeling (opt-in; never auto-ID
  of unknown people).
- Location clustering; mood and visual-theme search.
- "On this day" resurfacing via cama.temporal.
- Video scene indexing; screenshot and document understanding (OCR).
- Cross-platform post history and engagement analytics.
- Goal-based content recommendations; automatic weekly plans.
- Retrieval based on life chapters and ongoing narratives.
- Connections between personal memories, research, business, and creative work.

## Definition of done for the MVP

A folder of images becomes a searchable, connected memory the owner trusts
enough to plan real posts from, with every AI claim correctable and every
deletion real. Publishing integrations remain deferred until that trust exists.
