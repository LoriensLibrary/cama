"""CAMA Media Memory.

Persistent, relational, provenance-aware memory for personal media.

This subpackage turns a camera roll into connected memory. It stores lightweight
records and derived understanding for photos and videos, and connects each asset
to people, places, events, trips, emotional tone, narrative themes, published
posts, and engagement outcomes, without duplicating the asset across contexts.

Media Memory is a native CAMA module. It reuses the existing memory core
(cama.core, cama.memory), ingestion (cama.ingest), temporal recall
(cama.temporal), retrieval (see RETRIEVAL.md), and the API/SDK layers. The only
new external capability is perception: EXIF extraction, hashing, thumbnails, and
optional local vision tagging. All of that lives behind adapters in this package
and never runs inside the memory core.

Status: design scaffold. See docs/ for VISION, ARCHITECTURE, DATA_MODEL,
ROADMAP, TASKS, DECISIONS, and PRIVACY_SAFETY.
"""

__all__: list[str] = []
__status__ = "design-scaffold"
