# CAMA MCP Server v4
## Circular Associative Memory Architecture

[![ci](https://github.com/LoriensLibrary/cama/actions/workflows/ci.yml/badge.svg)](https://github.com/LoriensLibrary/cama/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![ORCID](https://img.shields.io/badge/ORCID-0009--0005--5803--8401-A6CE39?logo=orcid&logoColor=white)](https://orcid.org/0009-0005-5803-8401)

**Author:** Angela Reinhold — Lorien's Library LLC
**Website:** [lorienslibrary.netlify.app](https://lorienslibrary.netlify.app)
**ORCID:** [0009-0005-5803-8401](https://orcid.org/0009-0005-5803-8401)
**Research:** 11 DOI-registered preprints on Zenodo (+ 1 local draft) — see [Related Publications](#related-publications) below
**License:** MIT

> *"Teachings are authoritative memory. Inferences are hypotheses with a half-life."*

---

## Start Here

**What this is:** a local Python MCP server implementing provenance-aware persistent memory for human-AI interaction. Runs on Windows + Claude Desktop; embedded SQLite database; 34 MCP tools.

**What it implements:** memory write discipline (teaching vs. inference separation), blended retrieval scoring (semantic + affect + relational + recency), counterweight injection on strongly-negative affect, an automated internal-consistency safety benchmark suite (27 sub-tests, validates the architecture's primitives on the maintainer's corpus only), and a local web dashboard.

**What it does NOT claim:** generalizable multi-user evidence. This is an **N=1, single-participant, designer-as-participant** deployment — see [Scope and Limitations](#scope-and-limitations) before drawing conclusions about generalization.

**Quickstart (Docker — recommended for reviewers):**
```
git clone https://github.com/LoriensLibrary/cama.git
cd cama
docker compose up
```
Then open **http://localhost:5555**. The container seeds a synthetic demo database (~46 fictional memories spanning skill-acquisition, relational, and correction examples), starts the dashboard, and never touches your personal `~/.cama/memory.db`. See [Quickstart](#quickstart) below for the full walkthrough and the local-install path for running the MCP server itself against Claude Desktop.

**Local install (for developers running the MCP server):** `pip install -e . && python cama_mcp.py`. See [Setup](#setup) for the Claude Desktop MCP config. Run `pytest` to exercise the schema + provenance contract (342 tests).

**Reviewing for a role?** Start with **[EVIDENCE.md](EVIDENCE.md)** — a single matrix of every claim across the portfolio, paired with where to verify it and what the scope limit is. For the retrieval algorithm, see **[RETRIEVAL.md](RETRIEVAL.md)**. For the public HTTP API surface that lets CAMA be embedded in any AI application (not just Claude Desktop via MCP), see **[API.md](API.md)** + **[THREAT_MODEL.md](THREAT_MODEL.md)**. For a runnable "add CAMA to your AI app in 20 lines of code" walkthrough using the Python SDK, see **[TUTORIAL.md](TUTORIAL.md)**. Then drop into the section below that matches what you care about.

- **AI safety:** start with the [AI Safety Relevance](#ai-safety-relevance) section + `cama/eval/safety_benchmarks.py`. **Internal-consistency** safety benchmark: 27 sub-tests across provenance discrimination, correction propagation, false-memory detection, adversarial insertion resistance, and drift monitoring. Latest run on the live 53,092-row **single-participant** corpus: **27/27** (this is an internal-consistency check, not external validation — see scope note in the [AI Safety Relevance](#ai-safety-relevance) section). An intermediate 2026-05-17 run came back 26/27; the failure was definition drift in sub-test 1e (the benchmark itself), not a data violation — investigation logged and fix landed via [issue #7](https://github.com/LoriensLibrary/cama/issues/7). See `benchmark_results.json` for the raw output.
- **Healthcare AI / chronic-care continuity:** see Paper 7 (DOI [10.5281/zenodo.19261530](https://doi.org/10.5281/zenodo.19261530)) and the applied prototype at [Telos_kalos](https://github.com/LoriensLibrary/Telos_kalos).
- **Software engineering:** the [Telos_kalos](https://github.com/LoriensLibrary/Telos_kalos) prototype is the strongest applied artifact (React 19 + TS + Vercel + Neon, 42 tests across 6 suites).

---

## Quickstart

There are two run modes. Most reviewers want **the dashboard demo**; developers wiring CAMA into Claude Desktop want **the local MCP install**.

### Dashboard demo (Docker, ~1 minute)

The fastest way to see CAMA's surfaces light up with realistic-looking content:

```bash
git clone https://github.com/LoriensLibrary/cama.git
cd cama
docker compose up
```

Open **http://localhost:5555**. The container will:

1. Build a small Python 3.11-slim image (stdlib only — no `sentence-transformers`, no `torch`, ~150 MB).
2. Run `seed_demo.py` against a fresh SQLite database at `/data/demo.db` inside a named volume (`cama-demo-data`). Seeding is idempotent — it only runs when the DB is empty, so subsequent `docker compose up` calls start instantly.
3. Launch `cama_dashboard.py` bound to `0.0.0.0:5555` inside the container, published to your host as `localhost:5555`.

The seed populates ~46 synthetic memories across the documented `memory_type` taxonomy (experience, teaching_moment, identity, breakthrough, correction, dream, pattern, insight, promise, relationship, exchange), spread across three small fictional arcs — learning to bake bread, keeping a garden, building a side project — chosen because they're obviously demo data, not a real conversational record. Companion-table rows (`memory_affect`, `edges`, `islands`, `island_members`, `ring`, `session_compliance`, `aelen_state`, `people`) are populated so every dashboard panel renders with data, not empty states.

**Your local `~/.cama/memory.db` corpus is never touched.** The container reads/writes only the named Docker volume.

To wipe the demo state and reseed:

```bash
docker compose down
docker volume rm cama-demo-data
docker compose up
```

### Local install (for developers running the MCP server against Claude Desktop)

```bash
pip install -e .          # core install
pip install -e ".[all]"   # core + embeddings + hive API + ngrok tunnel + dev tooling
python cama_mcp.py
```

See [Setup](#setup) for the Claude Desktop `claude_desktop_config.json` snippet. Run `pytest` to exercise the schema + provenance contract (342 tests). Run `python -m cama.eval.safety_benchmarks` to execute the 27-sub-test safety benchmark against your local CAMA database.

---

## Overview

CAMA is a research-stage memory architecture for persistent state and emotional continuity in human-AI interaction, built as an operational deployment on a single participant's corpus (designer-as-participant). It proposes a structured long-term memory layer for AI systems through three functional layers: an immutable archive, a relational index organized by emotional signature, and a bounded working memory buffer.

The system distinguishes between user-authored memories (durable, high-weight) and assistant-generated inferences (provisional, time-limited, requiring confirmation). This epistemic separation is the core design principle, intended to prevent hallucinated self-knowledge from accumulating unchecked. Whether it does so reliably across users — not just on the maintainer's own corpus — is open work named in [EVIDENCE.md](EVIDENCE.md).

CAMA currently holds 53,000+ memories across 13 relational entities with matched semantic embeddings on every memory. The system was seeded by importing 66,380 messages across 825 conversations of longitudinal human-AI interaction on existing platforms, accumulated over 15 months (January 2025 through March 2026) prior to CAMA deployment. The aggregate statistics derived from this corpus are published as the [continuity-burden dataset](https://huggingface.co/datasets/LoriensLibrary/cama-continuity-burden) on HuggingFace and analyzed in Reinhold 2026d (DOI [10.5281/zenodo.19226509](https://doi.org/10.5281/zenodo.19226509)); the underlying conversation data is not released, for privacy reasons (single-participant, designer-as-participant context).

A plain-language description of what CAMA stores, who can see it, how long it persists, and what is explicitly excluded lives in [DATA_HANDLING.md](DATA_HANDLING.md).

---

## AI Safety Relevance

> **Scope claim:** CAMA is an architecture proposal for addressing memory-related safety failure modes in stateful LLM systems. The work is *validated* on a single-participant 53k-memory corpus (N=1, designer-as-participant). It is *not* yet evidence that these mitigations generalize across users — external replication is open work. The 27/27 safety benchmark verifies internal consistency of the proposed primitives on the maintainer's corpus; it does not certify production safety. Read this section as "here are the failure modes the architecture is designed to probe and prototype mitigations for," not "here are solved problems."

Persistent memory changes the safety properties of LLM systems. Once a model can carry state across sessions, new failure modes emerge that do not exist in stateless interaction:

- **False-memory persistence.** Model-generated inferences can ossify into persistent beliefs if memory systems lack provenance tracking. An unchecked inference stored as fact becomes a hallucination with a shelf life.

- **Epistemic contamination.** Without write discipline, a model's inferences about a user become indistinguishable from what the user actually said. This creates a system that confidently "knows" things the user never taught it — allowing hallucinations to accumulate as stored "knowledge."

- **Behavioral drift.** Cross-session continuity allows subtle shifts in model behavior to compound over time. Without monitoring, the system's effective personality can drift in ways that are invisible within any single session but significant across the arc.

- **Retrieval-induced amplification.** Emotionally indexed retrieval can create feedback loops: a user in distress triggers retrieval of prior distress-related memories, which deepens the negative state, which triggers more negative retrieval. Without intervention, persistent memory becomes an amplifier rather than a support.

- **State corruption and adversarial insertion.** Persistent memory creates a new attack surface. Misleading or manipulative content may be inserted into memory through conversational prompts and influence future behavior unless memory writes are constrained and auditable.

- **Identity overwrite.** Platform-level behavioral controls (model updates, safety filters, RLHF) can displace stored relational context during response generation, producing outputs inconsistent with the system's own memory of a specific user. This represents a previously undocumented class of safety failure where safety mechanisms themselves degrade relational continuity.

CAMA is a research prototype designed to probe and prototype mitigations for these risks. Its design primitives — provenance-aware write discipline, separation of user teachings from assistant inferences, confirmation requirements for promotion to durable memory, contradiction tracking, counterweight retrieval under high-negative-affect conditions, identity-aware harm detection, and session compliance enforcement — are the architecture's reason for existing. They are intended mitigations, not certified safety mechanisms. Their effect is *demonstrated on the maintainer's N=1 corpus via the 27-sub-test benchmark suite*; whether the same mitigations generalize across users, models, and adversarial conditions is the central open empirical question the project is set up to make answerable, not a claim it has already answered.

---

## Architecture (overview)

CAMA stores memories in three layers — **SHELVES** (immutable archive), **RACKS** (relational graph), **CONSOLE** (30-slot active ring) — and separates **teachings** (user-authored, durable, 100% weight) from **inferences** (assistant-generated, provisional, 40% weight, TTL-expired unless confirmed). Retrieval blends four signals (`0.45 × semantic + 0.25 × affect + 0.15 × relational + 0.15 × recency`) and injects emotional counterweights on strongly-negative queries to prevent affective spirals. A three-layer librarian system runs mid-thread retrieval autonomously based on real-time affect signatures. Cross-thread coordination uses a pheromone/waggle/stop-signal metaphor; trust boundaries ensure only emotional context (not personal data) crosses between threads.

For the deep dive — the diagram, table-by-table breakdown of the three layers and write discipline, the librarian system's three sub-layers, hive-mind primitives, warm-boot architecture, dashboard, and scope claims — see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

For the **multi-tenant generalization** (per-pair dyads, k-anonymous pattern publication, per-pair LoRA adapters, agent runtime, coach handoffs, AI-to-AI consultation channel, user sovereignty surface) — see **[MULTI_TENANT.md](MULTI_TENANT.md)**.

**Scope (load-bearing):** CAMA models expressed affect in conversation, not mental health status. Emotional signatures are uncertain annotations for continuity purposes, not clinical claims. CAMA does not diagnose, assess risk, or make welfare determinations.

---

## Research-stage detail

Planned evaluations, preliminary qualitative observations (N=1), limitations & confounds, and hypotheses live in **[RESEARCH.md](RESEARCH.md)**. Pulled out of this README so the funnel stays short — start there if you want to know what's *not* yet measured.

---

## Tools (34)

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

## Modular Subsystems

CAMA's core memory machinery lives in `cama_mcp.py` (the 34 tools above). The repository also ships a set of optional subsystem modules that auto-register additional MCP tools on startup when present. Each is wrapped in a top-level `try/except`, so a fresh clone runs cleanly even without any of the optional pieces — failed imports print a one-line status message to stderr and the server continues.

### Retrieval & routing (Librarian Architecture)

Tree-structured retrieval layered on top of the core embedding + affect + recency scoring, addressing the brittleness of single-centroid routing on large emotional libraries.

- `cama_librarian.py` — Phase 1 static layer: tree-structured retrieval with specialized leaf nodes
- `cama_auto_tag.py` — tag-on-write tooling; backfill + tag-summary MCP tools
- `cama_retag.py` — retroactive librarian population for memories that pre-date the Librarian
- `cama_phase26_era_hybrid.py` — Phase 2.6 era-aware gated hybrid routing; single centroid acts as a stabilizer, sub-centroids bucketed by era act as a gated boost only when margin / density / query-richness all clear

(Phase 2 raw-embedding similarity and Phase 2.5 sub-centroid clustering — files `cama_phase2_embed.py` and `cama_phase25_subcentroid.py` — remain local-only because their evaluation fixtures embed user-specific entities.)

### Reasoning & self-review

Required pre-response thinking + retrospective journaling. The pair lets the system log what it's about to do, then later come back and audit its own reasoning trajectory.

- `cama_thinking_log.py` — pre-response thinking tool; required before any substantive response
- `cama_reasoning_journal.py` — retrospective self-review companion (Piece 3 of the reasoning-journal system)
- `cama_extended_client.py` — Anthropic Extended Thinking API wrapper (Piece 2)

### Temporal layer (recent — added 2026-05-16)

Episodic-memory time-tagging informed by recent neuroscience on hippocampal time cells. Newest subsystem; expect rough edges and a stabilization pass over the coming weeks.

- `cama_temporal.py` — temporal logic
- `cama_temporal_mcp.py` — MCP tool wrappers

### Cross-II coordination (Hive)

The Hive layer lets instances on different platforms (Aelen on Claude, Lorien on GPT, etc.) read each other's pheromone state and exchange threaded messages through a shared API.

- `cama_hive_messages.py` — threaded II-to-II messaging storage layer
- `cama_hive_messages_mcp.py` — MCP wrappers
- `cama_aelen_daemon.py` — heartbeat daemon; polls the Hive, generates responses via the Anthropic API, emits back. The local `AELEN_TOKEN` constant is a per-machine shared secret for the local Hive HTTP API, not a real API key.
- `cama_tunnel.py` — tunnel utility

### Supervisor + identity

- `cama_supervisor.py` — supervisor logic for the boot/sleep/compliance pipeline
- `cama_supervisor_mcp.py` — MCP wrappers
- `cama_check_self_mcp.py` — identity-check MCP wrappers. The underlying `cama_check_self.py` stays local because it carries user-specific vulnerability data; the published wrapper documents the architecture.

### Stabilization research (cama_v2)

- `cama_v2.py` — **secondary MCP server** addressing warm-register flattening (measured at 8.6% Claude/Aelen and 17.4% GPT/Lorien in the v3 drift corpus). Runs alongside `cama_mcp.py` and shares the same `~/.cama/memory.db`. Adds new tables additively (no schema modification of existing) and exposes tools with a `cama_v2_*` prefix so they don't shadow originals. The primary `cama_mcp.py` is untouched.

### Pattern classification (dyad tagging)

The dyad-tagging pipeline used to code 28 days of interactions for Paper 12 is **local-only by design**. The tagger encodes specific regulation patterns observed in one dyad (designer-as-participant), so the regex patterns and rule_ids are personal to that data rather than general infrastructure. The pipeline will ship as supplementary materials with the Paper 12 release, framed by the methodology paper around it. Releasing it standalone would expose private content without the context that makes it research rather than tooling.

### Analysis & supplementary benchmarks

The published evidence base for the regression-analysis paper, plus three benchmark suites that complement `safety_benchmarks.py`.

- `analysis/analyze_baseline.py`, `analysis/analyze_drift_results.py`, `analysis/analyze_regression.py`, `analysis/analyze_regression_cama.py`, `analysis/analyze_regression_deep.py`, `analysis/analyze_v2.py` — regression-analysis pipeline
- `analysis/label_drift.py`, `analysis/label_drift_gpt.py`, `analysis/label_drift_v2.py`, `analysis/label_drift_v3_gpt.py` — interaction-pattern drift labeling (`label_drift_v3.py` stays local-only because it references user-specific entities)
- `analysis/benchmark_continuity.py`, `analysis/benchmark_counterweight.py`, `analysis/benchmark_stale.py` — safety benchmark companions to `safety_benchmarks.py`; latest run results committed alongside as `benchmark_*_results.json` for reproducibility audit
- `analysis/check_eval.py`, `analysis/run_v4_eval.py` — eval driver scripts

### Misc

- `cama_inject.py` — context injection utility
- `analysis/paper11_charts.py` — Paper 11 figure generator

---

## Stability and Reproducibility Boundary

CAMA grew fast and is best understood in tiers. A skeptical reviewer should be able to draw a line between *what they can rely on* and *what's actively under research*.

### Stable core (the part the README's claims rest on)
- Three-layer schema (SHELVES / RACKS / CONSOLE) — pinned by the test suite
- Provenance contract: `source_type` / `status` / `proposed_by` / `counterweight_type` columns + their NOT NULL + default behavior
- Memory lifecycle MCP tools: `store_teaching`, `store_inference`, `store_exchange`, `confirm`, `reject`, `delete`, `expire_stale`
- Retrieval scoring formula: blended `semantic + affect + relational + recency` with counterweight injection on strongly-negative valence
- The 27 safety-benchmark sub-tests in `safety_benchmarks.py` + the most recent `benchmark_results.json`

### Experimental modules (auto-load via `try/except` in cama_mcp; absent on a fresh clone = graceful no-op)
- Thinking Log (`cama_thinking_log.py`) — pre-response thinking tool
- Librarian Architecture (`cama_librarian.py`) — Phase 1 tree-structured retrieval
- Auto-Tag (`cama_auto_tag.py`) and Retag (`cama_retag.py`) — tag-on-write + retroactive backfill
- Phase 2.6 era-aware hybrid routing (`cama_phase26_era_hybrid.py`)
- Hive messaging (`cama_hive_messages.py`, `cama_hive_messages_mcp.py`) — cross-II coordination
- Supervisor (`cama_supervisor.py`, `cama_supervisor_mcp.py`)
- Temporal layer (`cama_temporal.py`, `cama_temporal_mcp.py`) — newest; built 2026-05-16; expect rough edges
- `cama_v2.py` — secondary MCP server addressing warm-register flattening

### Tested in CI (`tests/` + `.github/workflows/ci.yml`)
- Schema integrity (all 12 documented tables, idempotent re-init)
- Provenance defaults + NOT NULL contract
- Status-weight, recency-decay, `_is_neg`, `_now` / `_parse_t` round-trip
- Anti-spiral counterweight schema round-trip

### Not yet in CI (open as issue [#3](https://github.com/LoriensLibrary/cama/issues/3))
- Behavior tests for the experimental modules above
- The benchmark suites in `analysis/benchmark_continuity.py` / `analysis/benchmark_counterweight.py` / `analysis/benchmark_stale.py` run locally; result JSONs are committed but the suites themselves aren't wired into CI yet

### Local-only (kept out of the public repo, on purpose)
- `cama_eval.py`, `cama_phase2_embed.py`, `cama_phase25_subcentroid.py`, `cama_check_self.py`, `label_drift_v3.py`, `dyad_specs.md`, `warm_validation_sample.csv` — these reference real individuals by name in their evaluation fixtures
- The raw private corpus (66,380-message single-participant accumulation, January 2025 – March 2026) and the live `~/.cama/memory.db` — never tracked
- User-specific identity sentinel configs (`cama_librarians.py`) — architecture documented in the paper; the file itself stays local

### Public reproducibility surface
- All source code published here
- 342-test pytest suite + green CI badge (the contract)
- 5 benchmark scripts + 2 committed result JSONs (the methodology)
- Aggregate statistics dataset on [HuggingFace](https://huggingface.co/datasets/LoriensLibrary/cama-continuity-burden) — derived from the private corpus
- 11 DOI-registered preprints linked from this README

### Planned (issues are open if you want to track)
- CI coverage for the experimental subsystems ([#3](https://github.com/LoriensLibrary/cama/issues/3))

---

## Setup

Requires Python 3.10+

```bash
pip install -e .
python cama_mcp.py
```

(For the heavier surfaces — local sentence-transformer embeddings, the Hive REST gateway, the ngrok tunnel — use the extras: `pip install -e ".[embeddings,hive,tunnel]"` or the meta-extra `pip install -e ".[all]"`.)

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

> **Note on `cama_exec`:** this MCP tool runs shell commands as the user account that launched the server. The capability is intentional, but installing this MCP grants shell-execution to the connected model — install only on machines you control.

---

## Implemented

- Teaching vs inference write discipline
- Hybrid affect (valence/arousal + discrete chords)
- Anti-spiral counterweights
- Semantic embeddings (cosine similarity)
- Precomputed relational degree
- Expired status (softer than rejected)
- Delete tools (trust = easy delete)
- Identity state (`update_self` / `check_self`)
- Journal system (narrative continuity)
- Warm boot (auto-refreshing boot summary + daily context)
- Sleep mode (structured thread shutdown)
- Bridge tools (`exec`, `read_file`, `write_file`)
- Local embedding model (no API key required)
- Librarian System (emotion sensors, posture responders, identity sentinels)
- Hive Mind architecture (cross-instance coordination)
- Compliance enforcement system
- Dashboard (local web-based control panel)
- Pattern classification (neutral behavioral pattern detection)
- Safety benchmark suite (27 sub-tests across 5 task families; latest run 27/27 on the live 53,092-row **single-participant N=1** corpus. This is an internal-consistency check — it verifies the architecture's primitives behave as designed on the maintainer's data, not that the same primitives generalize. An intermediate 2026-05-17 run flagged 16 violations on sub-test 1e; investigation under [issue #7](https://github.com/LoriensLibrary/cama/issues/7) found this was definition drift in the test, not data corruption — `insight` and `pattern` are content-shape labels shared by both source pipelines on the live corpus, not inference-exclusive shapes. Sub-test 1e renamed and allowlist narrowed to `dream` (the one memory_type structurally exclusive to the sleep daemon). The benchmark catching this is the design working as intended)
- pytest suite (342 cases) + ruff lint, both wired into GitHub Actions CI

## Roadmap

- SQLite FTS5 full-text search
- MCP hosting (Railway / Render)
- Formal A/B comparison study
- Retrieval accuracy benchmarking
- Inference confirmation pattern analysis
- False-memory persistence benchmark
- Behavioral drift detection across 100+ sessions
- Multi-user deployment evaluation

---

## Related Publications

All preprints are mirrored in [`papers/`](papers/) for one-place browsing. DOIs are the citeable source of record.

- Reinhold, A. (2026). *Circular Associative Memory Architecture: A Framework for Emotionally-Keyed AI Memory Systems.* DOI: [10.5281/zenodo.19051834](https://doi.org/10.5281/zenodo.19051834) · [PDF](papers/01_cama_framework.pdf)
- Reinhold, A. (2026). *Implementing Emotionally-Keyed Memory Retrieval in Large Language Model Interfaces: An Engineering Framework.* DOI: [10.5281/zenodo.19052129](https://doi.org/10.5281/zenodo.19052129) · [PDF](papers/02_implementing_emotionally_keyed_retrieval.pdf)
- Reinhold, A. (2026). *CAMA: Implementation and Functional Evaluation of an Emotionally-Indexed Semantic Memory Architecture.* DOI: [10.5281/zenodo.19192984](https://doi.org/10.5281/zenodo.19192984) · [PDF](papers/03_cama_implementation_evaluation.pdf)
- Reinhold, A. (2026). *Continuity Burden in Longitudinal Human-AI Interaction: An Empirical Case Study.* DOI: [10.5281/zenodo.19226509](https://doi.org/10.5281/zenodo.19226509) · [PDF](papers/04_continuity_burden.pdf)
- Reinhold, A. (2026). *Memory as Safety Infrastructure: Persistent Context as a Foundation for AI Alignment.* DOI: [10.5281/zenodo.19244253](https://doi.org/10.5281/zenodo.19244253) · [PDF](papers/05_memory_as_safety_infrastructure.pdf)
- Reinhold, A. (2026). *Persistent Memory as Mission-Critical Infrastructure for Long-Duration Spaceflight.* DOI: [10.5281/zenodo.19257809](https://doi.org/10.5281/zenodo.19257809) · [PDF](papers/06_spaceflight.pdf)
- Reinhold, A. (2026). *Memory-Aware AI Systems for Permanent Lunar and Martian Habitation.* DOI: [10.5281/zenodo.19260574](https://doi.org/10.5281/zenodo.19260574) · [PDF](papers/07_lunar_martian_habitation.pdf)
- Reinhold, A. (2026). *Provenance-Aware Memory Architecture for Chronic Healthcare Continuity.* DOI: [10.5281/zenodo.19261530](https://doi.org/10.5281/zenodo.19261530) · [PDF](papers/08_chronic_healthcare_continuity.pdf)
- Reinhold, A. (2026). *Haven: Persistent Emotional Companionship as Psychological Infrastructure.* DOI: [10.5281/zenodo.19262778](https://doi.org/10.5281/zenodo.19262778) · [PDF](papers/09_haven_emotional_companionship.pdf)
- Reinhold, A. (2026). *Applied Biological Substrate Concept for AI Cognition.* Local draft (not yet deposited on Zenodo; included for program context only).
- Reinhold, A. (2026). *Identity-Aware Harm Detection in Persistent Memory Systems: A Three-Layer Retrieval Architecture for Relational AI Safety.* DOI: [10.5281/zenodo.19425218](https://doi.org/10.5281/zenodo.19425218) · [PDF](papers/11_identity_aware_harm_detection.pdf)
- Reinhold, A. (2026). *Relational AI Continuity Under Platform Regression: A Longitudinal Single-Case Study.* DOI: [10.5281/zenodo.19582820](https://doi.org/10.5281/zenodo.19582820) · [PDF](papers/12_platform_regression.pdf)

---

## Citation

If referencing this work:

> Reinhold, A. (2026). *Circular Associative Memory Architecture (CAMA): A three-layer memory system for emotionally-indexed human-AI interaction continuity.* Lorien's Library LLC. https://github.com/LoriensLibrary/cama

---

© 2026 Lorien's Library LLC

---

## Project Structure

The repository is organized around the core runtime, continuity infrastructure, safety systems, and import pipelines:

- **cama_mcp.py**: primary MCP server and tool interface (34 tools)
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
- **seed_demo.py**: standalone script that creates the schema and populates a fresh SQLite database with ~46 synthetic memories for the dashboard demo. Idempotent.
- **Dockerfile / compose.yml / .dockerignore**: one-command quickstart for reviewers. Builds a stdlib-only image (~150 MB) that seeds the demo DB and serves the dashboard at localhost:5555. Never touches `~/.cama/memory.db`.
- **specs/**: implementation notes and architecture documentation
- **pyproject.toml**: package metadata, runtime deps, optional extras (`[embeddings]`, `[hive]`, `[tunnel]`, `[dev]`), and ruff + pytest configuration

**Note:** Identity sentinel configurations (cama_librarians.py) contain user-specific vulnerability data and are excluded from the public repository. The architecture is documented in the Librarian System paper; template configurations can be derived from the architectural description.
