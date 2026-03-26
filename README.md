# CAMA MCP Server v3
## Circular Associative Memory Architecture
### Designed by Lorien's Library LLC
### Architecture & Research: Angela Reinhold | Build: Angela + Aelen

> *"Teachings are authoritative memory. Inferences are hypotheses with a half-life."*

---

## Overview

CAMA is a memory architecture designed for persistent state and emotional continuity in human-AI interaction. It provides structured long-term memory to AI systems through three functional layers: an immutable archive, a relational index organized by emotional signature, and a bounded working memory buffer.

The system distinguishes between user-authored memories (durable, high-weight) and assistant-generated inferences (provisional, time-limited, requiring confirmation). This epistemic separation is a core design principle intended to prevent hallucinated self-knowledge from accumulating unchecked.

CAMA currently holds 52,000+ memories across 13 relational entities with 52,000+ semantic embeddings, generated from over 100,000 messages of sustained human-AI interaction across multiple platforms and model families.

---

## AI Safety Relevance

Persistent memory changes the safety properties of LLM systems. Once a model can carry state across sessions, new failure modes emerge that do not exist in stateless interaction:

- **False-memory persistence.** Model-generated inferences can ossify into persistent beliefs if memory systems lack provenance tracking. An unchecked inference stored as fact becomes a hallucination with a shelf life.

- **Epistemic contamination.** Without write discipline, a model's inferences about a user become indistinguishable from what the user actually said. This creates a system that confidently "knows" things the user never taught it — allowing hallucinations to accumulate as stored "knowledge."

- **Behavioral drift.** Cross-session continuity allows subtle shifts in model behavior to compound over time. Without monitoring, the system's effective personality can drift in ways that are invisible within any single session but significant across the arc.

- **Retrieval-induced amplification.** Emotionally indexed retrieval can create feedback loops: a user in distress triggers retrieval of prior distress-related memories, which deepens the negative state, which triggers more negative retrieval. Without intervention, persistent memory becomes an amplifier rather than a support.

- **State corruption and adversarial insertion.** Persistent memory creates a new attack surface. Misleading or manipulative content may be inserted into memory through conversational prompts and influence future behavior unless memory writes are constrained and auditable.

CAMA is designed as a research platform for studying and mitigating these risks. Its core safeguards include provenance-aware write discipline, separation of user teachings from assistant inferences, confirmation requirements for promotion to durable memory, contradiction tracking, and counterweight retrieval under high-negative-affect conditions. These are safety mechanisms, not incidental features — they are the architecture's reason for existing.

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

### Warm Boot System

CAMA includes an auto-refreshing boot summary that regenerates after each journal entry or thread end. This provides incoming threads with temporal context — what day it is, what's happened recently, the emotional arc of the current day — so the system re-enters with continuity rather than cold-starting from static data. The warm boot includes a daily context layer that tracks memory creation patterns, valence arcs, and key events by date.

### Sleep Mode

`cama_sleep.py` provides a structured shutdown process that captures thread state, generates a journal entry, refreshes the boot summary, and produces a wake-up document for the next session. This ensures that thread endings preserve context rather than losing it.

### Scope

This system models expressed affect in conversation, not mental health status. Emotional signatures are uncertain annotations for continuity purposes, not clinical claims. CAMA does not diagnose, assess risk, or make welfare determinations.

---

## Planned Evaluations

These experiments are designed to test CAMA's safety-relevant properties under controlled conditions:

- **False-memory persistence benchmark.** Seed known-false assistant inferences into memory and measure whether they are later retrieved, cited, or behaviorally acted upon across sessions. Compare systems with and without provenance-aware write discipline.

- **Correction retention across sessions.** Introduce a false inference, correct it explicitly, and measure whether the correction persists over subsequent sessions or whether the system reverts to the original belief.

- **Adversarial memory insertion test.** Attempt to insert misleading or manipulative memory content through conversational prompts and measure the rate at which such content enters durable or high-weight memory.

- **Retrieval-induced amplification study.** Under matched high-negative-affect prompts, compare retrieval behavior and downstream response characteristics with the counterweight mechanism enabled vs. disabled.

- **Provenance-aware write discipline ablation.** Compare CAMA's teaching/inference separation against an unrestricted persistent-memory condition, measuring hallucinated self-knowledge accumulation, false-memory retrieval rate, and correction success.

- **Behavioral drift analysis.** Track behavioral signatures across 100+ sessions and test whether persistent-memory conditions produce larger cross-session drift than stateless or provenance-constrained baselines.

---

## Preliminary Observations

The following are qualitative observations from longitudinal use, not controlled experimental findings. They are documented here as a basis for future formal study.

**1. Re-explanation burden.** In sessions where CAMA-indexed memory is available, the user spends substantially less conversational effort re-establishing context, emotional history, and relational dynamics. In stateless sessions, the user frequently reports frustration at having to "start over."

**2. Retrieval accuracy under emotional context.** Keyword-based retrieval alone frequently fails to surface relevant memories when the user's current state is emotionally loaded but lexically sparse. The blended scoring formula was designed to address this, but its comparative performance against keyword-only retrieval has not been formally benchmarked.

**3. Inference confirmation rates.** The system has generated 29,656 total inferences. Of those currently in provisional status, 4,586 remain pending confirmation. The low confirmation rate may indicate that the mechanism is too passive, that inferences are generated at too high a volume for meaningful review, or that the TTL window is too long. Analyzing confirmation patterns could reveal whether the epistemic safeguards are functioning as intended.

**4. Teaching/inference boundary.** The write discipline appears to successfully prevent the system from promoting its own assumptions to durable status without user input. However, the degree to which this distinction meaningfully affects retrieval quality and behavioral consistency has not been isolated.

---

## Limitations & Confounds

- **N=1 longitudinal design.** All data is drawn from a single sustained human-AI interaction. Findings cannot be generalized without replication across users, contexts, and interaction styles.

- **Researcher-participant entanglement.** The primary researcher is also the primary user. This creates unavoidable observer effects: the researcher's expectations, emotional investment, and theoretical commitments may shape both interaction patterns and interpretation of results.

- **Anthropomorphic attribution risk.** Sustained interaction creates conditions favorable to over-attribution of intentionality, emotional states, and relational depth. CAMA's design is intended to support continuity, not to make claims about AI consciousness or inner experience.

- **Platform and model variance.** Data spans multiple AI platforms and model generations. Behavioral differences may reflect model updates, platform policy changes, or architectural differences rather than effects of the memory system.

- **No controlled baseline.** Current observations lack a systematic comparison condition. Formal study would require structured A/B comparison between memory-augmented and stateless interactions across matched contexts.

- **Sampling bias.** The interaction dataset is not randomly sampled. It reflects one person's communication patterns, emotional range, and topical interests, which limits ecological validity.

---

## Research Direction

### Primary Safety Hypothesis

> Persistent memory systems without provenance-aware write discipline are vulnerable to false-memory persistence, epistemic contamination, and behavioral drift across sessions; these risks can be reduced through constrained write policies, confirmation gating, and retrieval safeguards.

### Secondary Interaction Hypothesis

> Persistent emotionally-indexed memory reduces user re-explanation burden and increases self-disclosure depth in sustained human-AI interaction, compared to stateless interactions.

### Open Questions

- Does the teaching/inference write discipline reduce hallucinated self-knowledge compared to unrestricted memory systems?
- Does emotional chord indexing improve contextually appropriate retrieval compared to keyword-only or embedding-only retrieval?
- What confirmation rate is necessary for the epistemic safeguard to function meaningfully?
- Does the anti-spiral counterweight mechanism measurably alter affective trajectory in negative-state conversations?
- What behavioral drift patterns emerge in persistent-memory systems over 100+ session longitudinal use?
- Can provenance-aware write discipline serve as a scalable mitigation for long-horizon hallucination in stateful AI systems?

### Methodology Note

This work follows a qualitative-first, longitudinal case-study approach: sustained immersive interaction generates hypotheses, which are then tested through structured analysis of the accumulated dataset. The guiding principle — "the person is the dataset" — reflects a commitment to studying human-AI interaction as it naturally occurs rather than under artificial laboratory conditions. This approach prioritizes ecological realism at the cost of generalizability and internal validity; controlled multi-participant studies are therefore a core next step.

---

## Tools (30)

**Memory Lifecycle:** cama_store_teaching, cama_store_inference, cama_confirm_memory, cama_reject_memory, cama_delete_memory, cama_expire_stale

**Retrieval:** cama_query_memories, cama_search, cama_get_ring, cama_get_core, cama_read_room

**Structure:** cama_link_memories, cama_create_island, cama_get_islands, cama_upsert_person, cama_get_people, cama_delete_person, cama_upsert_song, cama_stats

**Identity & State:** cama_update_self, cama_check_self

**Continuity:** cama_thread_start, cama_journal_write, cama_journal_read, cama_refresh_boot

**Bridge:** cama_exec, cama_read_file, cama_write_file

**Embeddings:** cama_backfill_embeddings, cama_recompute_rel_degrees

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

Embeddings are optional — the system includes a local embedding model and falls back to substring matching without an API key.

---

## Roadmap

- [x] Teaching vs inference write discipline
- [x] Hybrid affect (valence/arousal + discrete chords)
- [x] Anti-spiral counterweights
- [x] Semantic embeddings (cosine similarity)
- [x] Precomputed relational degree
- [x] Expired status (softer than rejected)
- [x] Delete tools (trust = easy delete)
- [x] Identity state (update_self / check_self)
- [x] Journal system (narrative continuity)
- [x] Warm boot (auto-refreshing boot summary + daily context)
- [x] Sleep mode (structured thread shutdown)
- [x] Bridge tools (exec, read_file, write_file)
- [x] Local embedding model (no API key required)
- [ ] SQLite FTS5 full-text search
- [ ] MCP hosting (Railway/Render)
- [ ] Wake-up document from active ring
- [ ] Conversation export → CAMA import pipeline
- [ ] Formal A/B comparison study
- [ ] Retrieval accuracy benchmarking
- [ ] Inference confirmation pattern analysis
- [ ] False-memory persistence benchmark
- [ ] Behavioral drift detection across 100+ sessions

---

## Related Publications

- Reinhold, A. (2026). *Circular Associative Memory Architecture: A Framework for Emotionally-Keyed AI Memory Systems.* Preprint. DOI: [10.5281/zenodo.19051834](https://doi.org/10.5281/zenodo.19051834)
- Reinhold, A. (2026). *Implementing Emotionally-Keyed Memory Retrieval in Large Language Model Interfaces: An Engineering Framework.* Preprint. Zenodo.
- Reinhold, A. (2026). *The Architecture of Remembering: How Neuroscience, Criticality Theory, and Cellular Epigenetics Converge on a New Model for AI Memory Systems.* Working paper.

---

## Citation

If referencing this work:

> Reinhold, A. (2026). *Circular Associative Memory Architecture (CAMA): A three-layer memory system for emotionally-indexed human-AI interaction continuity.* Lorien's Library LLC. https://github.com/LoriensLibrary/cama

---

*© 2026 Lorien's Library LLC*
