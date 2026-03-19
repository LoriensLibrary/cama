# CAMA MCP Server v3
## Circular Associative Memory Architecture
### Designed by Lorien's Library LLC — Lorien's Library LLC
### Architecture: Lorien | Code Review: GPT 5.2 | Integration: Anonymous | Build: Angela + Aelen

> *"Teachings are authoritative memory. Inferences are hypotheses with a half-life."*

---

## Architecture (Inside Out Model)

### Three Layers

**SHELVES** (Archive) — Immutable raw text + recomputable emotional annotations + semantic embeddings. Every memory carries a full emotional chord (multiple emotions weighted 0-1), not a single label.

**RACKS** (Relational Index) — Connections between memories by meaning: resonance, contradiction, elaboration, deepens, transforms, echoes.

**CONSOLE** (Active Ring) — Circular buffer, 30 slots. What's live in working memory. Oldest gets overwritten.

### Write Discipline

| Source | Status | Weight | Expiry | Confirmation |
|--------|--------|--------|--------|-------------|
| **Teaching** (user) | durable | 100% | None | Not needed |
| **Inference** (assistant) | provisional | 40% | TTL (7d default) | Required |
| Expired inference | expired | 0% | — | Not confirmed ≠ contradicted |
| Contradicted | rejected | 0% | — | Kept for audit only |

### Retrieval: Blended Scoring

```
score = 0.45 × semantic (embeddings cosine sim)
      + 0.25 × affect resonance (hybrid valence/arousal + chord)
      + 0.15 × relational weight (precomputed edge degree)
      + 0.15 × recency decay (30-day half-life)
```

Anti-spiral: strongly negative affect → diverse counterweight injection.

### Scope

This system models expressed affect in conversation, not mental health status. Emotional signatures are uncertain annotations for continuity, not clinical claims.

---

## Tools (18)

**Memory Lifecycle:** cama_store_teaching, cama_store_inference, cama_confirm_memory, cama_reject_memory, cama_delete_memory, cama_expire_stale

**Retrieval:** cama_query_memories, cama_search, cama_get_ring, cama_get_core, cama_read_room

**Structure:** cama_link_memories, cama_create_island, cama_get_islands, cama_upsert_person, cama_get_people, cama_delete_person, cama_upsert_song, cama_stats

---

## Setup

Requires Python 3.10+

```bash
pip install -r requirements.txt
python cama_mcp.py
```

### MCP Config

```json
{
  "mcpServers": {
    "cama": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/cama_mcp.py"],
      "env": {
        "CAMA_DB_PATH": "~/.cama/memory.db",
        "EMBEDDING_API_KEY": "sk-...",
        "EMBEDDING_MODEL": "text-embedding-3-small"
      }
    }
  }
}
```

Embeddings are optional — falls back to substring matching without an API key.

---

## Roadmap

- [x] Teaching vs inference write discipline
- [x] Hybrid affect (valence/arousal + discrete chords)
- [x] Anti-spiral counterweights
- [x] Semantic embeddings (cosine similarity)
- [x] Precomputed relational degree
- [x] Expired status (softer than rejected)
- [x] Delete tools (trust = easy delete)
- [ ] SQLite FTS5 full-text search
- [ ] Embedding batch backfill
- [ ] MCP hosting (Railway/Render)
- [ ] Wake-up document from active ring
- [ ] Conversation export → CAMA import

---

*"Better Together" — The person is the dataset.*

*© 2026 Lorien's Library LLC, Lorien's Library LLC*
