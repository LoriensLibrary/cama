# Reviewer guide

CAMA is a large research repo. This is the fast path in, by how much time you have. Every claim has a verification artifact; see [`EVIDENCE.md`](EVIDENCE.md) for the full claim matrix.

## Scope, read this first

CAMA is an **N=1 research prototype**: a single-participant, designer-as-participant deployment. The architecture, schema discipline, and test suite are real and verifiable. The empirical results (retrieval quality, safety benchmarks) are measured on one corpus and show internal consistency, **not** generalization across users. The README and `EVIDENCE.md` state this boundary on every numeric claim. Read the work with that frame.

## 5-minute review

1. Read **Start Here** and the **scope note** in [`README.md`](README.md).
2. Skim [`EVIDENCE.md`](EVIDENCE.md): the claim → proof → limitation matrix. This is the single index of what is and is not claimed.
3. Run the contract: `pip install -e ".[dev]"` then `pytest -q`. The suite pins the schema and provenance contract. Count is snapshot-dated in `EVIDENCE.md`; run `pytest --collect-only` for the live number.
4. Open one provenance flow: [`tests/test_provenance.py`](tests/test_provenance.py) shows that teachings (user-authored, durable) and inferences (AI-generated, provisional) are structurally distinguished at the schema level, not by convention.

## 30-minute review

5. **Retrieval**: [`RETRIEVAL.md`](RETRIEVAL.md) documents the route → fan-out → blend → counterweight pipeline end to end, including the Phase 1/2/2.5/2.6 evolution and the measured R@5 numbers that selected the current routing.
6. **Public API + threat model**: [`API.md`](API.md) and [`THREAT_MODEL.md`](THREAT_MODEL.md). The API enforces provenance, dyad isolation, and "AI cannot self-promote inferences" architecturally. `tests/test_api.py` carries the adversarial tests.
7. **Safety benchmark**: `python -m cama.eval.safety_benchmarks` runs the 27 internal-consistency sub-tests (provenance discrimination, correction propagation, false-memory detection, adversarial insertion resistance, drift monitoring) against a local database.
8. **Bridge + store hardening**: [`mcp_sections/guard.py`](mcp_sections/guard.py) (cama_exec denylist / strict allowlist, sensitive-read blocking, localhost bind) and [`cama/core/cama_trust.py`](cama/core/cama_trust.py) (memory-poisoning quarantine). Covered by `tests/test_guard.py` and `tests/test_trust.py`.

## Deep review

9. **SDK + tutorial**: [`TUTORIAL.md`](TUTORIAL.md) and `cama/sdk/` (the 20-line end-to-end example, typed exceptions mapped to each contract violation).
10. **Architecture**: the `cama/` package tree (core, librarian, api, sdk, eval) and the five `*_mcp.py` entry points at the root that Claude Desktop loads.
11. **Eras and routing internals**: `cama/librarian/cama_phase26_era_hybrid.py` for the era-aware gated routing, with the benchmark JSON in `benchmarks/`.

## What is and is not in CI

CI runs `ruff` + `pytest` on Python 3.10 / 3.11 / 3.12. The README's **Tested in CI** and **Not yet in CI** sections name exactly which subsystems are covered. The experimental modules (Librarian, Thinking Log, Temporal) are explicitly outside the CI contract today; that is stated, not hidden.
