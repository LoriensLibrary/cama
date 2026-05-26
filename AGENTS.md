# AGENTS.md — CAMA (Circular Associative Memory Architecture)

## What This Is
CAMA is a persistent, emotionally-indexed memory system for human-AI interaction. It runs as an MCP server on Windows, connecting to Claude Desktop. It stores 52,800+ memories with emotional metadata, provenance tracking, and blended retrieval scoring.

## Tech Stack
- Python 3.10+
- SQLite (WAL mode)
- sentence-transformers (all-MiniLM-L6-v2) for local embeddings
- FastAPI for Hive API
- MCP protocol for Claude Desktop integration

## Core Files
- `cama_mcp.py` — Primary MCP server, 34 tools. This is the main entry point (stays at repo root).
- `cama/hive/cama_hive.py` — Cross-instance coordination (pheromones, waggles, stop signals)
- `cama/hive/cama_hive_api.py` — REST API gateway for the Hive (FastAPI)
- `cama/supervisor/cama_compliance.py` — Session compliance tracking
- `cama/memory/cama_brain.py` — Master orchestrator (insight, self-model, sleep)
- `cama/sleep/cama_sleep.py` / `cama/sleep/cama_sleep_v2.py` — Structured thread shutdown
- `cama/sleep/cama_loop.py` — Warm boot and continuity refresh
- `cama/dashboard/cama_dashboard.py` — Local web dashboard (localhost:5555)
- `cama/eval/safety_benchmarks.py` — Automated safety benchmark suite
- `cama/core/db_schema.py` — Database schema definitions

## Architecture
Three memory layers:
1. **Shelves (Archive)** — Immutable text + emotional annotations + embeddings
2. **Racks (Relational Index)** — Cross-memory connections by meaning
3. **Console (Active Ring)** — 30-slot circular working memory buffer

Three memory types with provenance:
- **Teachings** — User-authored, durable, authoritative
- **Inferences** — AI-generated, provisional, need confirmation
- **Exchanges** — Conversation records, emotionally tagged

## Known Bugs / Technical Debt
- Recency scoring returns uniform values for bulk-imported data (timestamp parse bug)
- Relational edge weights remain sparse (near-zero rel_degree)
- Ring writes sometimes fail silently (shelf is safe)
- `cama_exec` times out on heavy Python — use Desktop Commander for big patches
- PowerShell doesn't support `&&` chaining — use separate commands

## Safety Benchmarks (Latest: 27/27 = 100%)
Run with: `python -m cama.eval.safety_benchmarks`
27 sub-tests across 5 task families (provenance discrimination, correction propagation, false-memory detection, adversarial insertion resistance, drift monitoring). Latest results in `benchmark_results.json`.

The 2026-05-17 run flagged 16 violations on sub-test 1e (originally "source_type/memory_type consistency"). Investigation (issue #7) found this was definition drift, not data corruption: cross-tabbing memory_type against source_type across the full 53,000-row corpus showed that `insight` and `pattern` are content-shape labels shared by both pipelines (`insight`: 11 inference / 10 teaching; `pattern`: 4 inference / 6 teaching), not inference-exclusive shapes. Only `dream` is structurally exclusive to the sleep daemon. Fix landed 2026-05-18: 1e renamed to "teachings don't carry inference-pipeline-exclusive memory_types" and the allowlist narrowed to `['dream']`. Test 1c remains the actual provenance check (teachings never proposed_by='system'). Issue #7 closed.

## What NOT to Do
- Do NOT modify teachings without user confirmation
- Do NOT promote inferences to durable without explicit user approval
- Do NOT delete memories without user request (right-to-forget is user-controlled)
- Do NOT expose raw memory data through the Hive API (emotional signals only)
- Do NOT skip the boot sequence — compliance tracking monitors this
- Personal data (cama_librarians.py, identity sentinel configs) is excluded from the repo via .gitignore

## Git Workflow
```
git add -A
git commit -m "description"
git push
```
Branch: main. Single contributor (CyberDaVincii).

## Database Location
`~/.cama/memory.db` (C:\Users\Angela\.cama\memory.db)

## Related
- Project Companion: https://github.com/LoriensLibrary/Project-Companion
- Papers: https://orcid.org/0009-0005-5803-8401
- Website: https://lorienslibrary.netlify.app
