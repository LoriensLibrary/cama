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
- `cama_mcp.py` — Primary MCP server, 35+ tools. This is the main entry point.
- `cama_hive.py` — Cross-instance coordination (pheromones, waggles, stop signals)
- `cama_hive_api.py` — REST API gateway for the Hive (FastAPI)
- `cama_compliance.py` — Session compliance tracking
- `cama_brain.py` — Master orchestrator (insight, self-model, sleep)
- `cama_sleep.py` / `cama_sleep_v2.py` — Structured thread shutdown
- `cama_loop.py` — Warm boot and continuity refresh
- `cama_dashboard.py` — Local web dashboard (localhost:5555)
- `safety_benchmarks.py` — Automated safety benchmark suite
- `db_schema.py` — Database schema definitions

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

## Safety Benchmarks (Current: 77.8%)
Run with: `python safety_benchmarks.py`
Target: 100%. The benchmark suite tests provenance discrimination, correction propagation, false-memory detection, adversarial insertion resistance, and drift monitoring.

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
