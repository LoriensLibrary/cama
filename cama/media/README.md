# CAMA Media Memory

> Working name: CAMA Media Memory. Turn a camera roll into a connected,
> searchable memory system that helps preserve experiences and transform them
> into meaningful stories.

Media Memory is a **native CAMA module**, not a separate application. It extends
CAMA's persistent, provenance-aware, emotionally-indexed memory from
conversations to **personal media**: photos and videos, and the people, places,
events, trips, feelings, and stories inside them.

The problem is not a lack of content. It is that the meaning and relationships
inside that content are trapped in a camera roll. Media Memory's job is to make
that meaning **persistent, connected, and retrievable** over a growing history.

## The one-sentence architecture

**CAMA remembers; perception describes.** Everything about *remembering* (storage,
provenance, correction, forgetting, emotional indexing, associative and temporal
retrieval) reuses the existing CAMA core. The only new capability is *perception*
(EXIF, hashing, thumbnails, local vision tagging), which lives behind adapters in
this package and never runs inside the memory core.

```
media file  ->  perception (adapters)  ->  observations (provisional)
                                              |
                                              v
                    CAMA memory core  <->  associative + temporal retrieval
                    (provenance, emotion, correction, forgetting)
                                              |
                                              v
         entities: person / place / event / trip / theme / emotion
                                              |
                                              v
                    post  ->  publication  ->  engagement outcome
```

## Documents

| Doc | What it answers |
| --- | --- |
| [docs/VISION.md](docs/VISION.md) | Why this exists and what it is. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module boundaries; CAMA vs external services. |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Entities and memory relationships. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | MVP milestones (M0-M5) and beyond. |
| [docs/TASKS.md](docs/TASKS.md) | Prioritized backlog; TASK-001 is ready to start. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architectural decision records. |
| [docs/PRIVACY_SAFETY.md](docs/PRIVACY_SAFETY.md) | Privacy and safety requirements. |

## Team

- **Product owner:** feature decisions, testing, real-world feedback.
- **ChatGPT:** product vision, UX and workflow, captions, social strategy.
- **Claude:** architecture, data model, planning, docs, design reviews.
- **Codex:** implementation, ingestion, local media processing, search, tests, PRs.

## Status

Design scaffold. No runtime code yet. Integration points with `cama.core`,
`cama.memory`, and `cama.ingest` are marked **proposed** until verified against
the existing implementation (see DECISIONS.md, ADR-0002).
