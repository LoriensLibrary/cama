# DATA MODEL: CAMA Media Memory

This is the initial data model and the memory relationships. It is deliberately
layered **on top of** CAMA's existing memory and people/place graph rather than
beside it. Field lists are a starting point for Codex, not a frozen schema.

## 1. The core idea: one asset, many contexts, no duplication

A single photo of Apollo looking out the truck window should connect to Apollo,
road trips, family adventures, Florida, sunset, a calm mood, "travel companion,"
a specific trip, and future post themes such as freedom, home, growth, or
exploration. It must do so **without being copied** into each of those buckets.

So the asset is a **node**, and every context is an **edge** to another node.
This is the associative graph CAMA already models for memories; media adds new
node and edge types.

```
MediaAsset --appears_in--> Event --part_of--> Trip
MediaAsset --depicts------> Person (human or pet, explicit label)
MediaAsset --located_at---> Place
MediaAsset --feels---------> EmotionTone (valence, arousal, chord)
MediaAsset --evokes--------> NarrativeTheme (freedom, home, growth, ...)
MediaAsset --related_to----> MediaAsset (same moment / near-duplicate)
MediaAsset --published_as--> Post --on--> Platform
Post --resulted_in--------> Engagement
```

Every edge records **source** (`manual` or `ai`) and **confidence**, so
provenance is answerable for relationships, not just for the asset.

## 2. Entities

### MediaAsset (new)
The atom. One row per unique file (by sha256).

- `id` (pk)
- `sha256` (content identity; dedup key), `phash` (near-duplicate grouping)
- `media_type` (image | video), `mime`, `width`, `height`, `duration_s`
- `captured_at` (from EXIF; nullable), `imported_at`
- `gps_lat`, `gps_lng` (nullable; redactable on export)
- `camera_make`, `camera_model`
- `store_path` (content-addressed location of the preserved original)
- `thumb_path`
- `status` (new | kept | hidden | deleted)
- `sensitivity` (low | medium | high); `is_minor` (bool) forces high
- `ai_generated` (bool; supports platform AI-content disclosure)
- `import_id` (fk -> Import)

### Observation (new; maps to a CAMA inference)
A single machine-derived claim about an asset. Provisional until reviewed.

- `id` (pk), `asset_id` (fk)
- `kind` (scene | object | animal | landmark | caption | mood | aesthetic | ocr_text | face_region)
- `value` (text/json), `confidence` (0..1)
- `model`, `model_version`
- `state` (provisional | confirmed | rejected)
- `created_at`
- Provenance is always `ai`. Confirming an observation emits a durable teaching.

### Person (reuse cama people; extended)
Humans and pets. Populated only by **explicit user labeling**.

- `id` (pk), `name`, `kind` (human | dog | cat | fish | other)
- `role` (e.g., partner, child, dog), `is_minor` (bool)

### Place (reuse cama places; extended)
- `id` (pk), `name`, `lat`, `lng`, `radius_m` (for clustering)

### Event (new)
A time-plus-place cluster of assets: the natural seed of a story or a post.

- `id` (pk), `title`, `span_start`, `span_end`, `place_id` (fk), `summary`

### Trip (new)
A larger arc that groups events (a multi-day road trip, a vacation).

- `id` (pk), `title`, `start_date`, `end_date`, `summary`

### NarrativeTheme (new)
Cross-cutting story themes: freedom, home, growth, exploration, family, calm,
sobriety, learning, funny. Assets and events link to themes.

- `id` (pk), `name`, `slug`, `description`

### EmotionTone (reuse cama emotional index)
Stored the way CAMA stores affect for any memory.

- `valence` (-1..1), `arousal` (-1..1), `chord` (json: named emotions -> intensity)

### Post (new)
An intentional set of assets headed for a platform.

- `id` (pk), `status` (unused | draft | scheduled | posted | archived)
- `platform`, `format` (single | album | reel | story)
- `caption`, `hashtags` (json array)
- `scheduled_for`, `posted_at`, `external_url`

### Engagement (new)
Outcome snapshot for a published post; written back into memory.

- `id` (pk), `post_id` (fk), `platform`
- `reach`, `likes`, `comments`, `shares`, `saves`
- `fetched_at`

### Import (new)
Audit record for one ingest run.

- `id` (pk), `source_path`, `started_at`, `finished_at`
- `assets_seen`, `assets_new`, `assets_duplicate`

## 3. Edges (junction tables)

All carry `source` (manual | ai) and `confidence` unless noted.

- `asset_event` (asset_id, event_id)
- `event_trip` (event_id, trip_id)
- `asset_person` (asset_id, person_id)  # only ever manual in MVP
- `asset_place` (asset_id, place_id)
- `asset_theme` (asset_id, theme_id)
- `asset_emotion` (asset_id, emotion ref)
- `asset_asset` (asset_id, related_asset_id, relation: same_moment | near_duplicate)
- `post_asset` (post_id, asset_id, position)

## 4. Significance: "why this may matter later"

Reuse CAMA's counterweight/significance taxonomy so a memory can be tagged with
why it matters: `grounding`, `agency`, `connection`, `self_compassion`,
`evidence_of_progress`. This is what lets the system later surface "memories
related to graduating and starting my bachelor's degree" or resurface a moment
because it mattered, not because it was recent.

## 5. Worked example: the Apollo truck-window photo

```
MediaAsset#1042 (sha256 ..., captured 2025-11-03 17:42, gps near Boca Grande)
  depicts       -> Person: Apollo (dog)            [manual]
  appears_in    -> Event: "drive home at golden hour" [ai time+geo, confirmed]
  part_of(event)-> Trip: "Boca Grande, Nov 2025"    [manual]
  located_at    -> Place: "Boca Grande"             [ai from gps]
  feels         -> EmotionTone(valence +0.6, arousal -0.3, {calm:0.7, longing:0.3})
  evokes        -> NarrativeTheme: freedom, home     [ai suggested, unconfirmed]
  related_to    -> MediaAsset#1043 (same_moment)
  status: kept, never published -> answers "what have I never posted?"
```

The single row participates in eight contexts. "Show me every Florida sunset with
Apollo" is then a graph query: assets that `depicts` Apollo AND `located_at` a
Florida place AND have a sunset scene observation (confirmed) or golden-hour time.
