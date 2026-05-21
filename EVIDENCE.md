# Evidence Matrix

A single index of what this research program claims, where to verify each claim, what counts as proof, and what the limitation is.

The format follows a deliberate pattern: **claim → proof → limitation → next step.** Each row is scoped — narrow enough to be falsifiable, with the scope boundary written into the row itself rather than hidden in a footnote.

Snapshot date: **2026-05-21.** Numerical claims (test counts, memory counts) drift over time; use the linked artifacts as the source of truth.

---

## 1 — CAMA core (this repo)

| Claim | Verify here | Proof | Limitation / scope boundary |
|---|---|---|---|
| Persistent-memory MCP server, 34 tools, runs locally against Claude Desktop. | `cama_mcp.py` at repo root; [`AGENTS.md`](AGENTS.md) for tool list; `claude_desktop_config.json` snippet in [Setup](README.md#setup). | Tools register on startup; MCP handshake logged in `~/.cama/logs/`. | stdio transport only on Windows so far; macOS/Linux untested. |
| 187 pytest tests, all green, on Python 3.10 / 3.11 / 3.12. | [`tests/`](tests/), [`.github/workflows/ci.yml`](.github/workflows/ci.yml). | Latest CI run on `main`: green. Count verifiable via `pytest --collect-only`. | Tests cover schema, provenance, multi-tenant dyad layer, hive protocol, persona, quad, surface. They do **not** cover the experimental subsystems (Librarian, Thinking Log, Temporal) — see `Not yet in CI` in the README. |
| 27/27 safety benchmarks pass on the live 53,103-memory corpus. | [`cama/eval/safety_benchmarks.py`](cama/eval/safety_benchmarks.py), [`benchmarks/benchmark_results.json`](benchmarks/benchmark_results.json). | Run `python -m cama.eval.safety_benchmarks` against `~/.cama/memory.db`. | Single-participant (N=1) corpus. Benchmark measures internal consistency of CAMA's provenance/correction/retrieval contracts — **not** generalization across users. |
| Provenance discipline: teachings (user-authored, durable) are structurally distinguished from inferences (AI-generated, provisional) at the schema level. | [`cama/core/db_schema.py`](cama/core/db_schema.py); [`tests/test_provenance.py`](tests/test_provenance.py); [`tests/test_schema.py`](tests/test_schema.py). | NOT NULL columns on `proposed_by` and `source_type`; tests verify teachings never carry `proposed_by='system'`. | The discipline holds in the schema and the test suite; runtime drift would still be possible if a future writer bypassed the documented helpers. |
| Phase 2.6 era-aware gated hybrid routing won the April 29 internal benchmark. | [`cama/librarian/cama_phase26_era_hybrid.py`](cama/librarian/cama_phase26_era_hybrid.py) docstring; routing-confidence telemetry in `routing_confidence_log` table. | Phase 2 R@5 = 33.4%, Phase 2.5 R@5 = 24.8%, Phase 2.6 (with gate) recovered Phase-2-level stability while keeping era-aware aperture. | Benchmark ran on the same N=1 corpus. The result is "this routing variant works on Angela's data," not "this routing variant generalizes." |
| Counterweight retrieval for anti-spiral injection on negative affect. | [`cama/core/cama_v2.py`](cama/core/cama_v2.py) (`_is_neg`, counterweight pool selection); benchmark task 4 in [`benchmarks/benchmark_results.json`](benchmarks/benchmark_results.json). | Negative-affect injection pool: 4,389 candidates, mean valence +0.488 against a baseline mean of −0.983 — delta +1.471. | The pool is built from Angela's tagged counterweights. A new user would need their own counterweights tagged before the mechanism activates. |
| 14-subpackage code organization (after 2026-05-21 reorg). | [`cama/`](cama/) tree; the [`refactor/package-layout`](https://github.com/LoriensLibrary/cama/commits/main) merge commit. | `pip install -e .` works from a fresh clone; `git blame` survives the move (renames detected at 100% similarity). | Root still holds five `*_mcp.py` entry-point scripts so that Claude Desktop's `claude_desktop_config.json` doesn't need to change. Not a flat package. |
| Modern Python packaging: `pyproject.toml` with core deps + optional extras (embeddings, hive, tunnel, dev). | [`pyproject.toml`](pyproject.toml). | `pip install -e .` and `pip install -e ".[all]"` both succeed. | The 5 root `*_mcp.py` scripts are *not* declared as console entry points — they're invoked by absolute path from Claude Desktop's MCP config, not via `pip`-installed shims. |
| Retrieval algorithm is documented end-to-end (route → fan-out → blend → counterweight → return), including the Phase 1 / 2 / 2.5 / 2.6 evolution and the empirical R@5 numbers that selected Phase 2.6. | [`RETRIEVAL.md`](RETRIEVAL.md). | The doc cites every file behind each stage (`cama_librarian.py`, `cama_phase26_era_hybrid.py`, `cama_v2.py`, etc.) and the measured latency JSON. | Document explains the Phase-1 keyword routing path in depth. Phase-2.x semantic routing is described but its end-to-end latency at scale is open work (separate benchmark not yet run). |
| Retrieval latency: p50 43 ms, p99 61 ms against the 53k-memory live corpus. | [`cama/eval/benchmark_retrieval_latency.py`](cama/eval/benchmark_retrieval_latency.py); [`benchmarks/benchmark_retrieval_latency.json`](benchmarks/benchmark_retrieval_latency.json). | 100 timed runs (20 generic queries × 5 trials) against `cama.librarian.cama_librarian.retrieve`, after a warmup call. Output JSON contains only timing percentiles — no query content, no memory IDs, no result text. Source-listed queries are deliberately generic so the methodology is reproducible. | Measures Phase-1 keyword routing + per-librarian SQL fan-out. Does **not** include Phase-2.x semantic embedding routing — that has a different cost profile (sentence-transformer compute) and warrants its own benchmark. Numbers also reflect Angela's laptop hardware; absolute values would differ on other machines, but the *shape* (sub-100ms p99 at this corpus size, low stdev) is the load-bearing claim. |
| Lint baseline enforced in CI: ruff (pyflakes + isort) on the entire codebase. | [`pyproject.toml`](pyproject.toml) `[tool.ruff]`; [`.github/workflows/ci.yml`](.github/workflows/ci.yml) `lint` job. | `ruff check .` passes; CI runs it on Python 3.12. | Conservative ruleset (`select = ["F", "I"]`) — pyflakes and isort only. Style rules (E/W) deliberately deferred; the codebase has pre-existing line-length and one-statement-per-line debt that's not worth pulling into the first lint baseline. |

## 2 — Telos_kalos (applied prototype)

Independent applied artifact built for the [Kalos Health Software Engineer](https://github.com/LoriensLibrary/Telos_kalos) role; **not affiliated with Kalos Health**. Live demo: [telos-kalos.vercel.app](https://telos-kalos.vercel.app).

| Claim | Verify here | Proof | Limitation / scope boundary |
|---|---|---|---|
| Modern React stack (React 19 + TypeScript + Vite), deployed live on Vercel. | [`LoriensLibrary/Telos_kalos`](https://github.com/LoriensLibrary/Telos_kalos) `package.json` + Vercel deployment URL. | Public deployment serves the app; CI runs lint + typecheck + test + build. | Demo runs on synthetic data; no real PHI. |
| 42 tests across 6 suites, CI green. | [`LoriensLibrary/Telos_kalos/.github/workflows/`](https://github.com/LoriensLibrary/Telos_kalos). | GitHub Actions history. | Tests are component / integration level; no end-to-end browser tests against the deployed instance. |
| Serverless Claude integration via Vercel Functions, with a draft-review AI workflow rather than auto-apply. | Vercel Function definitions in the repo; UI shows AI-drafted suggestions that the user explicitly accepts or rejects. | Live demo demonstrates the draft → review → accept flow. | Demo uses a synthetic 12-week chronic-care dataset; production use would need real auth, audit logging, consent gating, HIPAA-ready hosting, and SOC 2 evidence. |
| CAMA proof layer wired in. | Repo links to this CAMA repo; the prototype reads provenance metadata from a synthetic CAMA-shaped store. | Code paths visible in the Telos repo. | The prototype demonstrates the integration *shape*; it does not run a live CAMA instance against real participant data. |

## 3 — Project Companion (K-12 design prototype)

Design study, **not** a deployed platform. See [`LoriensLibrary/Project-Companion`](https://github.com/LoriensLibrary/Project-Companion).

| Claim | Verify here | Proof | Limitation / scope boundary |
|---|---|---|---|
| K-12 education platform built on CAMA primitives — design prototype level. | [`LoriensLibrary/Project-Companion`](https://github.com/LoriensLibrary/Project-Companion) README, including the explicit "design prototype, not deployed" framing. | Repo README labels each surface as `implemented` / `mock` / `roadmap`. | Persistence, accounts, COPPA controls, content moderation, age verification, mandatory-reporting logic, right-to-delete, accessibility audit, and CAMA *write* integration are all **roadmap**, not shipped. |
| Mock-tutor mode disables live Anthropic calls unless the developer explicitly opts in. | Repo README "Mock-tutor mode" section; the toggle code path. | A casual cloner runs the mock and cannot accidentally invoke a live LLM. | The opt-in path *does* call Anthropic; deploying live mode to minors without the surrounding safety surface would be inappropriate, and the README says so. |

## 4 — Published research

| Claim | Verify here | Proof | Limitation / scope boundary |
|---|---|---|---|
| 11 DOI-registered preprints on Zenodo (2026-03 / 2026-04), all authored under [ORCID 0009-0005-5803-8401](https://orcid.org/0009-0005-5803-8401). | ORCID record; Zenodo entries; citation list in this README's "Related Publications" section. | DOIs resolve; each paper is publicly accessible. | These are *preprints*, not peer-reviewed publications. Several papers describe architecture and provenance contracts that were then implemented in CAMA. |
| Paper 7 — **"Provenance-Aware Memory Architecture for Chronic Healthcare Continuity"**, the most directly relevant artifact for healthcare-AI continuity reviewers. | DOI [10.5281/zenodo.19261530](https://doi.org/10.5281/zenodo.19261530). | DOI resolves to a fully-cited preprint with abstract, architecture, and threat model. | Preprint, not peer-reviewed. |

## 5 — Source dataset

| Claim | Verify here | Proof | Limitation / scope boundary |
|---|---|---|---|
| Aggregate statistics (continuity-burden study, ~15 kB of JSON) published on HuggingFace. | [`LoriensLibrary/cama-continuity-burden`](https://huggingface.co/datasets/LoriensLibrary/cama-continuity-burden). | Dataset card + JSON files publicly downloadable. | **Aggregate only.** The underlying 66,380-message single-participant source corpus (January 2025 – March 2026) is *not* released. Replication of empirical claims is not possible from the published artifact. |
| 53,103 memories currently live in the local CAMA database the benchmarks run against. | [`benchmarks/benchmark_results.json`](benchmarks/benchmark_results.json) `total_memories` field. | Output of the latest safety benchmark run. | Live DB grows; this count is a snapshot of 2026-05-21. The DB itself is gitignored (single-participant content). |
| CAMA *runtime* is recent (~2 months as of 2026-05-21); the *source corpus* spans 15 months pre-CAMA. | This README's "CAMA timeline distinction"; the architecture / RESEARCH docs. | Sleep-cycle logs (`~/.cama/sleep.log`) start 2026-03; the 66,380 messages were imported at deployment. | Do **not** read this as "15 months of CAMA in production." It is "CAMA in production since March 2026, seeded by 15 months of accumulated chat data from prior platforms." |

---

## What this matrix is NOT

It is not a marketing document. It is not a list of capabilities. It is a list of **claims with the proof and the scope boundary attached.**

If a row makes a claim that you cannot verify by clicking the link, that is a bug — file an issue. The intent is that any skeptical reviewer can refute or accept each row in isolation, without having to read the rest of the portfolio first.
