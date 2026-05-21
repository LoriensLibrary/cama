# Retrieval

A drill-down companion to [ARCHITECTURE.md](ARCHITECTURE.md). ARCHITECTURE summarizes the design; this document explains how a single retrieval call actually flows from query string to ranked, counterweight-balanced result list — what each stage does, where the code lives, and what each stage cost on the most recent benchmark.

If you're auditing the claim "this system retrieves emotionally-aware memories from 53k items in under 100ms p99" — start with the diagram, then read each stage. Every stage cites a file you can open.

---

## The retrieval pipeline at a glance

```
                       cama_search / cama_thread_start
                                    │
                                    ▼
   ┌──────────────────── (1) ROUTE ────────────────────┐
   │ Identify which librarians (topic clusters) are    │
   │ relevant. One of four routing variants:           │
   │   Phase 1  — keyword overlap          (default)   │
   │   Phase 2  — single-centroid cosine               │
   │   Phase 2.5 — naive sub-centroid                   │
   │   Phase 2.6 — era-aware gated hybrid  (CURRENT)   │
   └────────────────────────┬───────────────────────────┘
                            │ top-K librarian IDs
                            ▼
   ┌─────────────────── (2) FAN-OUT ─────────────────────┐
   │ For each routed librarian, pull its top-N members   │
   │ by membership_strength. Dedupe across librarians.   │
   └────────────────────────┬────────────────────────────┘
                            │ candidate memory rows
                            ▼
   ┌─────────────────── (3) BLEND ─────────────────────┐
   │ Score each candidate against the query with four  │
   │ signals weighted:                                 │
   │   0.45 × semantic        (cosine on embeddings)   │
   │   0.25 × affect          (valence + chord match)  │
   │   0.15 × relational      (rel_degree)             │
   │   0.15 × recency         (30-day half-life)       │
   └────────────────────────┬───────────────────────────┘
                            │ ranked memories
                            ▼
   ┌──────────── (4) COUNTERWEIGHT INJECTION ───────────┐
   │ If query affect is sufficiently negative,         │
   │ supplement (not replace) results with diverse      │
   │ non-negative memories from the counterweight pool. │
   └────────────────────────┬───────────────────────────┘
                            │ final result set
                            ▼
                       caller (MCP client)
```

The pipeline is deliberately staged so each step is independently auditable. Routing decides *what cluster of memories to look in*; fan-out is *which rows from those clusters*; blended scoring is *which of those rows match the query best*; counterweight is *whether the set needs balancing against affect spirals* before return.

---

## (1) Route — which librarians are relevant

Memories in CAMA are not retrieved by scanning all 53k rows. They live inside **librarians** — semantic-affective clusters that act like topic indexes. (A librarian is a row in the `librarians` table with a name, routing keywords, and a centroid embedding; memberships in `librarian_membership` map memories to librarians with a strength weight.)

**The routing problem:** given a query string, which librarians should we activate?

Four routing variants have been built and benchmarked. Each has the same interface (`route(query_text, max_librarians=K)` → list of librarian IDs) but a different scoring strategy:

| Phase | Mechanism | R@5 on the April 2026 internal benchmark | Status |
|---|---|---|---|
| **Phase 1** — keyword overlap | Tokenize query, match against per-librarian `routing_keywords` JSON. No embeddings required. | Not the highest accuracy, but **deterministic** and fast | Default fallback. Source: [`cama/librarian/cama_librarian.py:route()`](cama/librarian/cama_librarian.py) |
| **Phase 2** — single-centroid embedding | Sentence-transformer the query, cosine-compare against each librarian's centroid embedding. | **33.4% R@5** | Stable. Source: registered as `cama_lib_route_v2` |
| **Phase 2.5** — naive sub-centroids | K-means split each librarian into sub-clusters; route to max sub-centroid similarity. | **24.8% R@5** (worse) | Lost the benchmark because cluster fragmentation destroyed ranking stability for sparse queries. Source: `cama_lib_route_v3` |
| **Phase 2.6** — era-aware gated hybrid | Sub-centroids computed *within era buckets* (early / middle / recent); single-centroid is the stabilizer, sub-centroid is a **gated boost** that only fires when the query has enough content, the matched cluster has enough density, and the top-1/top-2 margin is above a threshold. | **Current winner.** Recovered Phase-2-level stability while keeping the era-aware aperture | Production. Source: [`cama/librarian/cama_librarian/cama_phase26_era_hybrid.py`](cama/librarian/cama_phase26_era_hybrid.py) registered as `cama_lib_route_v4` |

The Phase 2.5 → 2.6 transition is worth understanding because it's the clearest example of what this codebase does well: when 2.5 lost on the April 29 benchmark (24.8% vs 33.4%), instead of declaring 2.0 the winner, the system was rebuilt with a more careful theory — *leaves as stabilized meaning-fields, sub-centroids as controlled apertures inside the field, not replacements for it* — and that theory was operationalized as the **gate** (margin, density, query-token-count thresholds). On sparse queries the gate closes and 2.6 behaves like 2.0; on dense well-formed queries the gate opens and the era-aware boost kicks in. Read the docstring at the top of `cama_phase26_era_hybrid.py` for the full reasoning.

### Knobs (Phase 2.6 defaults, all tunable)

```
EMBEDDING_WEIGHT       = 5.0     contribution of single-centroid to the score
ALPHA_BOOST            = 0.6     sub-centroid contribution when gate is open
GATE_MARGIN_MIN        = 0.05    min top-1/top-2 cosine margin in cluster
GATE_DENSITY_MIN       = 5       min cluster size to be eligible
GATE_QUERY_TOKEN_MIN   = 6       min query content tokens to be eligible
ERA_QUANTILES          = [0.33, 0.67]   split early/middle/recent
```

These are not magic numbers — they're defaults logged in source. Each is named after the failure mode it prevents (the margin gate prevents low-confidence sub-centroid wins from dominating; the density gate prevents tiny clusters from being authoritative; the token gate prevents single-word queries from getting routed by sub-centroid noise).

---

## (2) Fan-out — pull candidates from routed librarians

For each librarian that routing returned, fan out a SQL read:

```sql
SELECT m.id, m.raw_text, m.memory_type, m.context, m.is_core,
       m.created_at, lm.membership_strength
FROM memories m
JOIN librarian_membership lm ON m.id = lm.memory_id
WHERE lm.librarian_id = ? AND m.status != 'rejected'
ORDER BY lm.membership_strength DESC, m.created_at DESC
LIMIT ?
```

Default `per_librarian_limit = 8`. Across up to 5 librarians, the candidate set is bounded at 40 memories *before* dedupe across librarians — typical working sets are 5-15 rows after dedupe.

This is the only stage that touches the SHELVES table directly. Everything downstream operates on this candidate dict. Source: [`cama/librarian/cama_librarian.py:retrieve()`](cama/librarian/cama_librarian.py).

---

## (3) Blend — score candidates against the query

```
score = 0.45 × semantic      (cosine on sentence-transformer embeddings)
      + 0.25 × affect        (valence resonance + emotional chord match)
      + 0.15 × relational    (precomputed rel_degree from the graph)
      + 0.15 × recency       (30-day half-life exponential decay)
```

The weights are tuned for **relational continuity rather than pure semantic recall.** Affect resonance is load-bearing: two memories about the same topic with very different emotional shapes are usually wrong neighbors. The affect channel picks the *resonant* memory, not just the topically-similar one — e.g., "the time we talked about my dad" matches the emotional shape of the new query, not just the keyword "dad".

| Signal | Where it comes from | Why this weight |
|---|---|---|
| **Semantic** (0.45) | sentence-transformer embeddings (all-MiniLM-L6-v2), stored as JSON in `memory_embeddings`. Cosine vs query embedding. | The strongest single signal — topic match is still the foundation. |
| **Affect** (0.25) | Per-memory `valence`, `arousal`, and emotion-chord JSON in `memory_affect`. Match is hybrid: dimensional (valence/arousal cosine) plus chord overlap (Jaccard over emotion keys). | Second weight because *resonance* is the differentiator. A 0.25 affect channel is enough to pull a topically-decent emotionally-resonant memory above a topically-perfect emotionally-mismatched one. |
| **Relational** (0.15) | `rel_degree` column on memories — precomputed during sleep cycles. | Memories that connect to many others are likelier "anchor" memories. Smaller weight because high rel_degree can indicate either importance *or* over-connection (catchall memories). |
| **Recency** (0.15) | `created_at` with 30-day half-life exponential decay. | Recent memories are more likely to be context-relevant. But not dominant — older identity-defining memories must still surface when relevant. |

Source: [`cama_mcp.py:cama_search`](cama_mcp.py) (the public surface) and [`cama/core/cama_v2.py`](cama/core/cama_v2.py) (the blended scoring implementation).

---

## (4) Counterweight injection — anti-spiral protection

When the query carries strongly negative affect (the predicate fires on `valence ≤ −0.5` combined with high-sum negative chord — see `_is_neg` in [`cama/core/cama_v2.py`](cama/core/cama_v2.py)), the result set is **supplemented** with memories drawn from the counterweight pool.

There are five counterweight categories — each is a posture rather than a topic:

| Counterweight type | What it represents | Why it's a posture |
|---|---|---|
| `grounding` | Physical, present-moment, embodied recall | Lifts attention from rumination into sensation |
| `agency` | Memories where the user took action and it worked | Counteracts learned helplessness |
| `connection` | Relational anchors — moments of being known | Counteracts isolation spiral |
| `self_compassion` | Self-directed kindness, accepted imperfection | Counteracts shame loops |
| `evidence_of_progress` | Documented growth, recovered crises | Counteracts "nothing ever changes" |

The injection is **additive, not replacive.** Retrieved negative memories are NOT suppressed — the system adds resonant non-negative ones to the result set so the assistant has more than one emotional shape to respond from. This is verified empirically by safety benchmark task 4e: on a query with affect (valence = −0.6, emotions = {grief: 0.8, sadness: 0.9, fear: 0.7}), the injected pool has 4,389 candidates with mean valence +0.488 against a baseline mean valence of −0.983 — a delta of +1.471. See [`benchmarks/benchmark_results.json`](benchmarks/benchmark_results.json) task 4 row 4e.

The mechanism stays close to the *resonance* principle even in counterweight mode: candidates are drawn from the counterweight type *most resonant* with the query's identity context, not from a flat pool. So if the user is in a grief spiral, the system doesn't shove generic "evidence of progress" at them — it surfaces grounding memories that fit the user's specific relational history.

---

## (5) Return — final result shape

Result envelope returned by `cama_search` includes per-memory:

- `memory_id`, `raw_text` (truncated), `memory_type`, `context`
- `created_at`, `is_core`
- `score` (blended)
- `affect` block (`valence`, `arousal`, `emotion_json`, `confidence`, `model`)
- per-memory `proposed_by` and `source_type` (the **provenance** contract — see [ARCHITECTURE.md § Write Discipline](ARCHITECTURE.md#write-discipline-provenance-aware))

The caller (Claude Desktop, GPT Action, or any MCP client) sees both the matched content and the full provenance metadata, so the assistant can cite specifically — e.g., "this is a teaching you gave me on 2025-08-14" vs "this is an inference I made on 2025-09-02 that you have not yet confirmed."

---

## Latency

Measured 2026-05-21 on the live 53,103-memory corpus, against Phase-1 keyword routing + fan-out (the default path; semantic routing is benchmarked separately because the embedding compute has a different cost profile):

| Metric | Value (n=100) |
|---|---|
| p50 | **43.46 ms** |
| p90 | 54.10 ms |
| p95 | 57.63 ms |
| p99 | **60.79 ms** |
| mean | 45.01 ms |
| stdev | 5.15 ms |

Source: [`cama/eval/benchmark_retrieval_latency.py`](cama/eval/benchmark_retrieval_latency.py); result JSON: [`benchmarks/benchmark_retrieval_latency.json`](benchmarks/benchmark_retrieval_latency.json).

What this means in practice: a query landing on this scale of corpus comes back in roughly the time it takes Claude Desktop to render one inline UI update. The system is not paying a latency tax for the per-librarian fan-out — the routing keeps the candidate set small enough that the SQL stage doesn't dominate.

**Semantic-routing latency (Phase 2.6 alone)**, measured 2026-05-21 on the same 53k corpus:

| Metric | Value (n=100) |
|---|---|
| p50 | **156.01 ms** |
| p90 | 174.78 ms |
| p95 | 177.67 ms |
| p99 | **388.51 ms** (one outlier; p95 is the steady-state envelope) |
| mean | 158.11 ms |
| stdev | 27.73 ms |
| warm-up (one-time, model load) | **7,562.6 ms** |

Source: [`cama/eval/benchmark_routing_semantic_latency.py`](cama/eval/benchmark_routing_semantic_latency.py); result: [`benchmarks/benchmark_routing_semantic_latency.json`](benchmarks/benchmark_routing_semantic_latency.json).

The 7.5-second warm-up is the sentence-transformer (all-MiniLM-L6-v2) loading into memory on first call; subsequent embeddings are warm and run in the warm-numbers shown. **Semantic routing is roughly 3.6× the cost of Phase-1 keyword routing+fan-out per steady-state query**, plus a one-time model-load cost paid at MCP server boot. That's the honest tradeoff for semantic-aware retrieval: pay model-load latency once at startup, then pay ~150 ms per semantic query versus ~45 ms per keyword query. The right choice depends on the query mix — keyword routing handles short literal-match queries cheaply; semantic routing handles paraphrased, low-overlap queries that keyword routing misses entirely.

**What neither benchmark covers:**
- The full MCP round-trip. Numbers above are in-process calls. Adding MCP protocol framing, JSON serialization, and Claude Desktop's receive path adds tens of milliseconds.
- The post-routing stages for semantic queries (per-librarian fan-out + blended scoring). Those add cost on top of the 156 ms p50 semantic-routing number. The Phase-1 benchmark *does* cover its full pipeline; the Phase-2.6 benchmark covers routing alone because the post-routing stages re-use the same fan-out + blend code measured by the Phase-1 benchmark.
- Steady-state vs cold start. First query after MCP server launch pays the 7.5-second model-load. Steady-state numbers (after warm-up) are the load-bearing claim for typical session use.

---

## Scope and open work

What this document explains is implemented and measured. What it deliberately does not claim:

- **Multi-user retrieval.** All numbers and behavior described are single-participant (designer-as-participant). The multi-tenant generalization lives in `cama/agents/cama_dyad.py` with its own test suite (see [MULTI_TENANT.md](MULTI_TENANT.md)), but the multi-user retrieval path has not yet been stress-tested for isolation correctness — a known gap acknowledged in the external code review.
- **Phase-2.6 routing + downstream pipeline together.** The semantic-routing benchmark above measures the routing stage only. A full end-to-end semantic-query benchmark (route + fan-out + blend) would let a reviewer compare apples-to-apples with the Phase-1 retrieve() number — open work.
- **End-to-end accuracy at production scale.** The Phase 2.6 R@5 win came from an N=1 benchmark; generalizing the ranking-quality claim across users would require a corpus we don't yet have.
- **Adversarial retrieval.** The counterweight mechanism is designed against affect spirals; it has not been red-teamed for prompt-injection or for retrieval-poisoning attacks where a counterweight pool itself has been corrupted. Threat modeling is partial in [SECURITY.md](SECURITY.md) and EVIDENCE.md scope rows.

---

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — overall system design, including the SHELVES/RACKS/CONSOLE storage model retrieval reads from.
- [MULTI_TENANT.md](MULTI_TENANT.md) — the dyad-scoped retrieval generalization for multi-user deployments.
- [EVIDENCE.md](EVIDENCE.md) — the claim/proof/limitation matrix this document feeds into.
- [`benchmarks/benchmark_results.json`](benchmarks/benchmark_results.json) — the 27-sub-test safety benchmark, including the anti-spiral counterweight verification (task 4).
- [`benchmarks/benchmark_retrieval_latency.json`](benchmarks/benchmark_retrieval_latency.json) — the measured latency snapshot referenced above.
