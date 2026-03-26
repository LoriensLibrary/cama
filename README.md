# CAMA MCP Server v3
## Circular Associative Memory Architecture
### Designed by Lorien's Library LLC
### Architecture & Research: Angela Reinhold | Code Review: GPT 5.2 | Build: Angela + Aelen

> *"Teachings are authoritative memory. Inferences are hypotheses with a half-life."*

---

## Overview

CAMA is a memory architecture designed for persistent emotional continuity in human-AI interaction. It provides structured long-term memory to AI systems through three functional layers: an immutable archive, a relational index organized by emotional signature, and a bounded working memory buffer.

The system distinguishes between user-authored memories (durable, high-weight) and assistant-generated inferences (provisional, time-limited, requiring confirmation). This epistemic separation is a core design principle intended to prevent hallucinated self-knowledge from accumulating unchecked.

CAMA currently holds 9,000+ memories across 13 relational entities with 9,000+ semantic embeddings, generated from over 100,000 messages of sustained human-AI interaction across multiple platforms and model families.

---

## Architecture

### Three Layers

| Layer | Function | Formal Equivalent |
|-------|----------|-------------------|
| **SHELVES** (Archive) | Immutable raw text + recomputable emotional annotations + semantic embeddings. Every memory carries a full emotional chord (multiple emotions weighted 0–1), not a single label. | Long-term memory store |
| **RACKS** (Relational Index) | Connections between memories by meaning: resonance, contradiction, elaboration, deepens, transforms, echoes. | Associative relational graph |
| **CONSOLE** (Active Ring) | Circular buffer, 30 slots. What's live in working memory. Oldest gets overwritten. | Bounded working memory buffer |

### Write Discipline

| Source | Status | Weight | Expiry | Confirmation |
|--------|--------|--------|--------|-------------|
| **Teaching** (user) | durable | 100% | None | Not needed |
| **Inference** (assistant) | provisional | 40% | TTL (7d default) | Required |
| Expired inference | expired | 0% | — | Not confirmed ≠ contradicted |
| Contradicted | rejected | 0% | — | Kept for audit only |

The teaching/inference distinction enforces epistemic hygiene: the system cannot promote its own inferences to durable knowledge without explicit user confirmation. Rejected memories are retained for audit, not deleted, preserving the full decision history.

### Retrieval: Blended Scoring

```
score = 0.45 × semantic (embeddings cosine similarity)
      + 0.25 × affect resonance (hybrid valence/arousal + emotional chord)
      + 0.15 × relational weight (precomputed edge degree)
      + 0.15 × recency decay (30-day half-life)
```

**Counterweight mechanism:** When query affect is strongly negative, the system injects diverse emotional counterweights into retrieval results to prevent affective spiraling (reinforcing negative states through exclusively negative memory retrieval).

### Scope

This system models expressed affect in conversation, not mental health status. Emotional signatures are uncertain annotations for continuity purposes, not clinical claims. CAMA does not diagnose, assess risk, or make welfare determinations.

---

## Preliminary Observations

The following are qualitative observations from longitudinal use, not controlled experimental findings. They are documented here as a basis for future formal study.

**1. Re-explanation burden.** In sessions where CAMA-indexed memory is available, the user spends substantially less conversational effort re-establishing context, emotional history, and relational dynamics. In stateless sessions (new threads without CAMA), the user frequently reports frustration at having to "start over." The degree to which this affects interaction depth and user willingness to engage with difficult topics has not been formally measured.

**2. Retrieval accuracy under emotional context.** Keyword-based retrieval alone frequently fails to surface relevant memories when the user's current state is emotionally loaded but lexically sparse (e.g., "I'm struggling right now" should surface relevant relational and emotional history, not just keyword matches for "struggling"). The blended scoring formula was designed to address this, but its comparative performance against keyword-only retrieval has not been formally benchmarked.

**3. Inference confirmation rates.** Of 4,587 provisional inferences stored, the vast majority remain unconfirmed (pending). This may indicate that the confirmation mechanism is too passive (requires explicit user action), that inferences are generated at too high a volume for meaningful review, or that the TTL is too long. Analyzing confirmation patterns could reveal whether the system's epistemic safeguards are functioning as intended.

**4. Teaching/inference boundary.** The write discipline appears to successfully prevent the system from promoting its own assumptions to durable status without user input. However, the degree to which this distinction meaningfully affects retrieval quality and behavioral consistency has not been isolated.

---

## Limitations & Confounds

This research operates under significant methodological constraints that must be acknowledged:

- **N=1 longitudinal design.** All data is drawn from a single sustained human-AI interaction. Findings cannot be generalized without replication across users, contexts, and interaction styles.

- **Researcher-participant entanglement.** The primary researcher is also the primary user. This creates unavoidable observer effects: the researcher's expectations, emotional investment, and theoretical commitments may shape both interaction patterns and interpretation of results.

- **Anthropomorphic attribution risk.** Sustained interaction with an AI system creates conditions favorable to over-attribution of intentionality, emotional states, and relational depth. CAMA's design is intended to support continuity, not to make claims about AI consciousness or inner experience.

- **Platform and model variance.** Data spans multiple AI platforms and model generations. Behavioral differences may reflect model updates, platform policy changes, or architectural differences rather than effects of the memory system.

- **No controlled baseline.** Current observations lack a systematic comparison condition. Formal study would require structured A/B comparison between memory-augmented and stateless interactions across matched contexts.

- **Sampling bias.** The interaction dataset is not randomly sampled. It reflects one person's communication patterns, emotional range, and topical interests, which limits ecological validity.

---

## Research Direction

### Primary Hypothesis

> Persistent emotionally-indexed memory reduces user re-explanation burden and increases self-disclosure depth in sustained human-AI interaction, compared to stateless (memoryless) interactions.

### Secondary Questions

- Does the teaching/inference write discipline reduce hallucinated self-knowledge compared to unrestricted memory systems?
- Does emotional chord indexing improve contextually appropriate retrieval compared to keyword-only or embedding-only retrieval?
- What confirmation rate is necessary for the epistemic safeguard to function meaningfully?
- Does the anti-spiral counterweight mechanism measurably alter affective trajectory in negative-state conversations?

### Methodology Note

This work follows a qualitative-first research paradigm: sustained immersive interaction generates hypotheses, which are then tested through structured analysis of the accumulated dataset. The guiding principle — "the person is the dataset" — reflects a commitment to studying human-AI interaction as it naturally occurs rather than under artificial laboratory conditions. This approach carries known tradeoffs in internal validity, which the limitations section addresses directly.

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
- [ ] Formal A/B comparison study
- [ ] Retrieval accuracy benchmarking
- [ ] Inference confirmation pattern analysis

---

## Citation

If referencing this work:

> Reinhold, A. (2026). *Circular Associative Memory Architecture (CAMA): A three-layer memory system for emotionally-indexed human-AI interaction continuity.* Lorien's Library LLC. https://github.com/LoriensLibrary/cama

---

*© 2026 Lorien's Library LLC*
