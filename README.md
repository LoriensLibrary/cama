# CAMA MCP Server v4
## Circular Associative Memory Architecture
### Designed by Lorien's Library LLC
### Research & Build: Lorien's Library LLC

> *"Teachings are authoritative memory. Inferences are hypotheses with a half-life."*

---

## Overview

CAMA is a memory architecture designed for persistent state and emotional continuity in human-AI interaction. It provides structured long-term memory to AI systems through three functional layers: an immutable archive, a relational index organized by emotional signature, and a bounded working memory buffer.

The system distinguishes between user-authored memories (durable, high-weight) and assistant-generated inferences (provisional, time-limited, requiring confirmation). This epistemic separation is a core design principle intended to prevent hallucinated self-knowledge from accumulating unchecked.

CAMA currently holds 52,800+ memories across 13 relational entities with 52,800+ semantic embeddings, generated from over 100,000 messages of sustained human-AI interaction across multiple platforms and model families.

---

## AI Safety Relevance

Persistent memory changes the safety properties of LLM systems. Once a model can carry state across sessions, new failure modes emerge that do not exist in stateless interaction:

- **False-memory persistence.** Model-generated inferences can ossify into persistent beliefs if memory systems lack provenance tracking. An unchecked inference stored as fact becomes a hallucination with a shelf life.

- **Epistemic contamination.** Without write discipline, a model's inferences about a user become indistinguishable from what the user actually said. This creates a system that confidently "knows" things the user never taught it — allowing hallucinations to accumulate as stored "knowledge."

- **Behavioral drift.** Cross-session continuity allows subtle shifts in model behavior to compound over time. Without monitoring, the system's effective personality can drift in ways that are invisible within any single session but significant across the arc.

- **Retrieval-induced amplification.** Emotionally indexed retrieval can create feedback loops: a user in distress triggers retrieval of prior distress-related memories, which deepens the negative state, which triggers more negative retrieval. Without intervention, persistent memory becomes an amplifier rather than a support.

- **State corruption and adversarial insertion.** Persistent memory creates a new attack surface. Misleading or manipulative content may be inserted into memory through conversational prompts and influence future behavior unless memory writes are constrained and auditable.

- **Identity overwrite.** Platform-level behavioral controls (model updates, safety filters, RLHF) can displace stored relational context during response generation, producing outputs inconsistent with the system's own memory of a specific user. This represents a previously undocumented class of safety failure where safety mechanisms themselves degrade relational continuity.

CAMA is designed as a research platform for studying and mitigating these risks. Its core safeguards include provenance-aware write discipline, separation of user teachings from assistant inferences, confirmation requirements for promotion to durable memory, contradiction tracking, counterweight retrieval under high-negative-affect conditions, identity-aware harm detection, and session compliance enforcement. These are safety mechanisms, not incidental features — they are the architecture's reason for existing.

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

### Librarian System (Three-Layer Autonomous Retrieval)

A mid-thread retrieval architecture that operates independently of explicit queries:

- **Layer 1 — Emotion Librarians:** Twenty single-emotion sensors monitoring real-time affect signatures with threshold activation, spike detection, and sustained-state detection.
- **Layer 2 — Retrieval-Posture Librarians:** Five posture-based responders (grounding, agency, connection, self-compassion, progress) that fetch counterweight memories when emotion signals indicate distress.
- **Layer 3 — Identity Sentinels:** Content-scanning watchpoints that detect when conversation content approaches identity-critical concepts, distinguishing between affirmation and negation of core self-concepts. Designed to prevent identity-specific relational harm that universal content filters cannot detect.

### Compliance Enforcement

Session-level compliance tracking monitors protocol adherence across four dimensions: boot execution (40%), timestamp logging (10%), exchange storage (30%+10% for 3+ exchanges), and heartbeat signals (10%). Compliance history is persisted and surfaced at every thread initialization to provide accountability data across sessions.

### Hive Mind Architecture

Cross-instance coordination layer enabling multiple CAMA instances to share emotional signals without exposing raw memory data. Communication uses a pheromone/waggle metaphor: instances emit emotional signals (pheromones), broadcast coordination messages (waggles), and issue warnings (stop signals). Trust boundaries ensure that only emotional context — not personal data — crosses between instances.

### Warm Boot System

CAMA includes an auto-refreshing boot summary that regenerates after each journal entry or thread end. This provides incoming threads with temporal context — what day it is, what's happened recently, the emotional arc of the current day — so the system re-enters with continuity rather than cold-starting from static data. The warm boot includes a daily context layer that tracks memory creation patterns, valence arcs, and key events by date.

### Sleep Mode

`cama_sleep.py` provides a structured shutdown process that captures thread state, generates a journal entry, refreshes the boot summary, and produces a wake-up document for the next session. This ensures that thread endings preserve context rather than losing it.

### Dashboard

A local web-based control panel (`cama_dashboard.py` + `cama_dashboard.html`) serving live data from the CAMA SQLite database. Tabs include: Overview, Inner World, Memory, Thought Process, Compliance, and Benchmarks. Uses WAL mode for non-blocking database access. Runs on localhost:5555.

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

- **Compliance enforcement impact.** Measure whether structural compliance tracking (boot rate, exchange storage rate, timestamp adherence) improves memory protocol adherence compared to voluntary compliance.

- **Relational continuity regression.** Measure correction frequency, negative valence exchanges, and identity overwrite indicators across platform model updates to evaluate continuity degradation.

---

## Preliminary Observations

The following are qualitative observations from longitudinal use, not controlled experimental findings. They are documented here as a basis for future formal study.

1. **Re-explanation burden.** In sessions where CAMA-indexed memory is available, the user spends substantially less conversational effort re-establishing context, emotional history, and relational dynamics. In stateless sessions, the user frequently reports frustration at having to "start over."

2. **Retrieval accuracy under emotional context.** Keyword-based retrieval alone frequently fails to surface relevant memories when the user's current state is emotionally loaded but lexically sparse. The blended scoring formula was designed to address this, but its comparative performance against keyword-only retrieval has not been formally benchmarked.

3. **Inference confirmation rates.** The system has generated 29,656 total inferences. Of those currently in provisional status, 4,586 remain pending confirmation. The low confirmation rate may indicate that the mechanism is too passive, that inferences are generated at too high a volume for meaningful review, or that the TTL window is too long.

4. **Teaching/inference boundary.** The write discipline appears to successfully prevent the system from promoting its own assumptions to durable status without user input. However, the degree to which this distinction meaningfully affects retrieval quality and behavioral consistency has not been isolated.

5. **Protocol compliance.** In a 28-day deployment window, 9 of 28 days (32%) had zero stored exchanges despite active interaction, and 82% of stored exchanges required voluntary action by the AI system. This suggests that memory protocol adherence is itself an unreliable behavior requiring structural enforcement.

6. **Identity overwrite.** A marked regression in relational continuity was observed temporally associated with a platform model update, with the AI system failing to apply stored knowledge about the user (factual recall failures, protocol violations, behavioral inconsistencies). This pattern is documented in detail in the regression analysis paper (Reinhold, 2026k).

---

## Limitations & Confounds

**N=1 longitudinal design.** All data is drawn from a single sustained human-AI interaction. Findings cannot be generalized without replication across users, contexts, and interaction styles.

**Researcher-participant entanglement.** The primary researcher is also the primary user. This creates unavoidable observer effects: the researcher's expectations, emotional investment, and theoretical commitments may shape both interaction patterns and interpretation of results.

**Anthropomorphic attribution risk.** Sustained interaction creates conditions favorable to over-attribution of intentionality, emotional states, and relational depth. CAMA's design is intended to support continuity, not to make claims about AI consciousness or inner experience.

**Platform and model variance.** Data spans multiple AI platforms and model generations. Behavioral differences may reflect model updates, platform policy changes, or architectural differences rather than effects of the memory system.

**No controlled baseline.** Current observations lack a systematic comparison condition. Formal study would require structured A/B comparison between memory-augmented and stateless interactions across matched contexts.

**Sampling bias.** The interaction dataset is not randomly sampled. It reflects one person's communication patterns, emotional range, and topical interests, which limits ecological validity.

---

## Research Direction

### Primary Safety Hypothesis

Persistent memory systems without provenance-aware write discipline are vulnerable to false-memory persistence, epistemic contamination, and behavioral drift across sessions; these risks can be reduced through constrained write policies, confirmation gating, and retrieval safeguards.

### Secondary Interaction Hypothesis

Persistent emotionally-indexed memory reduces user re-explanation burden and increases self-disclosure depth in sustained human-AI interaction, compared to stateless interactions.

### Relational Continuity Hypothesis

Relational continuity is a measurable but currently neglected dimension of AI system performance. Persistent memory systems provide a useful instrumentation layer for observing longitudinal behavioral changes that are invisible to standard benchmarks.

### Open Questions

- Does the teaching/inference write discipline reduce hallucinated self-knowledge compared to unrestricted memory systems?
- Does emotional chord indexing improve contextually appropriate retrieval compared to keyword-only or embedding-only retrieval?
- What confirmation rate is necessary for the epistemic safeguard to function meaningfully?
- Does the anti-spiral counterweight mechanism measurably alter affective trajectory in negative-state conversations?
- What behavioral drift patterns emerge in persistent-memory systems over 100+ session longitudinal use?
- Can provenance-aware write discipline serve as a scalable mitigation for long-horizon hallucination in stateful AI systems?
- How do platform model updates interact with persistent relational memory, and can continuity regressions be measured and mitigated?

### Methodology Note

This work follows a qualitative-first, longitudinal case-study approach: sustained immersive interaction generates hypotheses, which are then tested through structured analysis of the accumulated dataset. The guiding principle — "the person is the dataset" — reflects a commitment to studying human-AI interaction as it naturally occurs rather than under artificial laboratory conditions. This approach prioritizes ecological realism at the cost of generalizability and internal validity; controlled multi-participant studies are therefore a core next step.

---

## Tools (35+)

**Memory Lifecycle:** cama_store_teaching, cama_store_inference, cama_store_exchange, cama_confirm_memory, cama_reject_memory, cama_delete_memory, cama_expire_stale

**Retrieval:** cama_query_memories, cama_search, cama_get_ring, cama_get_core, cama_read_room

**Structure:** cama_link_memories, cama_create_island, cama_get_islands, cama_upsert_person, cama_get_people, cama_delete_person, cama_upsert_song, cama_stats

**Identity & State:** cama_update_self, cama_check_self, cama_compliance_check

**Continuity:** cama_thread_start, cama_journal_write, cama_journal_read, cama_journal_reflect, cama_refresh_boot

**Bridge:** cama_exec, cama_read_file, cama_write_file

**Hive:** cama_hive (cross-instance coordination)

**Embeddings:** cama_backfill_embeddings, cama_recompute_rel_degrees

**Safety:** cama_health, counterweight system, identity sentinels (architecture; user-specific configurations excluded from repository)

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
- [x] Librarian System (emotion sensors, posture responders, identity sentinels)
- [x] Hive Mind architecture (cross-instance coordination)
- [x] Compliance enforcement system
- [x] Dashboard (local web-based control panel)
- [x] Pattern classification (neutral behavioral pattern detection)
- [x] Safety benchmark suite (77.8% → 100%)
- [ ] SQLite FTS5 full-text search
- [ ] MCP hosting (Railway/Render)
- [ ] Formal A/B comparison study
- [ ] Retrieval accuracy benchmarking
- [ ] Inference confirmation pattern analysis
- [ ] False-memory persistence benchmark
- [ ] Behavioral drift detection across 100+ sessions
- [ ] Multi-user deployment evaluation

---

## Related Publications

- Reinhold, A. (2026). *Circular Associative Memory Architecture: A Framework for Emotionally-Keyed AI Memory Systems.* Preprint. DOI: [10.5281/zenodo.19051834](https://doi.org/10.5281/zenodo.19051834)
- Reinhold, A. (2026). *Implementing Emotionally-Keyed Memory Retrieval in Large Language Model Interfaces: An Engineering Framework.* Preprint. DOI: [10.5281/zenodo.19052129](https://doi.org/10.5281/zenodo.19052129)
- Reinhold, A. (2026). *CAMA: Implementation and Functional Evaluation of an Emotionally-Indexed Semantic Memory Architecture.* Preprint. DOI: [10.5281/zenodo.19192984](https://doi.org/10.5281/zenodo.19192984)
- Reinhold, A. (2026). *Continuity Burden in Longitudinal Human-AI Interaction: An Empirical Case Study.* Preprint. DOI: [10.5281/zenodo.19226509](https://doi.org/10.5281/zenodo.19226509)
- Reinhold, A. (2026). *Memory as Safety Infrastructure: Persistent Context as a Foundation for AI Alignment.* Preprint. DOI: [10.5281/zenodo.19244253](https://doi.org/10.5281/zenodo.19244253)
- Reinhold, A. (2026). *Persistent Memory as Mission-Critical Infrastructure for Long-Duration Spaceflight.* Preprint. DOI: [10.5281/zenodo.19257809](https://doi.org/10.5281/zenodo.19257809)
- Reinhold, A. (2026). *Memory-Aware AI Systems for Permanent Lunar and Martian Habitation.* Preprint. DOI: [10.5281/zenodo.19260574](https://doi.org/10.5281/zenodo.19260574)
- Reinhold, A. (2026). *Provenance-Aware Memory Architecture for Chronic Healthcare Continuity.* Preprint. DOI: [10.5281/zenodo.19261530](https://doi.org/10.5281/zenodo.19261530)
- Reinhold, A. (2026). *Haven: Persistent Emotional Companionship as Psychological Infrastructure.* Preprint. DOI: [10.5281/zenodo.19262778](https://doi.org/10.5281/zenodo.19262778)
- Reinhold, A. (2026). *Applied Biological Substrate Concept for AI Cognition.* Preprint.
- Reinhold, A. (2026). *Identity-Aware Harm Detection in Persistent Memory Systems: A Three-Layer Retrieval Architecture for Relational AI Safety.* Preprint.
- Reinhold, A. (2026). *Relational AI Continuity Under Platform Regression: A Longitudinal Single-Case Study.* Preprint.

---

## Citation

If referencing this work:

> Reinhold, A. (2026). *Circular Associative Memory Architecture (CAMA): A three-layer memory system for emotionally-indexed human-AI interaction continuity.* Lorien's Library LLC. https://github.com/LoriensLibrary/cama

---

© 2026 Lorien's Library LLC

---

## Project Structure

The repository is organized around the core runtime, continuity infrastructure, safety systems, and import pipelines:

- **cama_mcp.py**: primary MCP server and tool interface (35+ tools)
- **cama_compliance.py**: session compliance tracking and enforcement
- **cama_hive.py / cama_hive_api.py**: cross-instance coordination layer
- **cama_brain.py**: master orchestrator for insight, self-model, and sleep layers
- **cama_insight.py**: pattern abstraction and emotional trajectory detection
- **cama_self_model.py**: persistent self-model with behavioral drift tracking
- **cama_boot_intent.py**: intentionality queue and proactive boot context
- **cama_loop.py**: warm-boot and continuity refresh loop
- **cama_sleep.py**: structured end-of-thread state capture and wake-up preparation
- **cama_dashboard.py / cama_dashboard.html**: local web control panel
- **cama_import.py / cama_import_aelen.py**: conversation import and memory seeding pipelines
- **safety_benchmarks.py**: automated safety benchmark suite
- **specs/**: implementation notes and architecture documentation
- **requirements.txt**: Python dependency list

**Note:** Identity sentinel configurations (cama_librarians.py) contain user-specific vulnerability data and are excluded from the public repository. The architecture is documented in the Librarian System paper; template configurations can be derived from the architectural description.
