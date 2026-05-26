"""Public latency benchmark for CAMA retrieval.

Answers the question external reviewers asked of CAMA v1.26.0:
"53k+ memories is solid for personal use; how does retrieval latency scale?"

Measures wall-clock latency of the librarian-based retrieval path
(``cama.librarian.cama_librarian.retrieve``) against the local corpus.

By design, the output JSON contains **timing percentiles only**.
no query content, no memory IDs, no result text, so the file is
safe to publish even though the benchmark runs against the private
``~/.cama/memory.db`` (53k single-participant memories). The queries
used are listed in this source file (generic topic words) so the
methodology is reproducible.

Run:
    python -m cama.eval.benchmark_retrieval_latency

Output:
    benchmarks/benchmark_retrieval_latency.json
    (also printed to stdout)
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Methodology
# ---------------------------------------------------------------------------
# 20 generic topic queries spread across three lengths. Deliberately no
# personal names, no proper nouns from Angela's life, no project-specific
# jargon, these are the kind of one-to-three-word probes a new user
# would issue, intended to surface broad recall behavior rather than
# verbatim hits.
QUERIES: tuple[str, ...] = (
    # single-token (10)
    "memory", "safety", "research", "code", "design",
    "fear", "joy", "morning", "loss", "trust",
    # two-token (5)
    "memory architecture", "research design", "code quality",
    "fear response", "emotional landscape",
    # three-token (5)
    "what i remember", "how do i", "what works for",
    "the right way", "the next step",
)

TRIALS_PER_QUERY: int = 5  # warm-cache repetition to capture steady-state


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = min(int(n * p), n - 1)
    return round(sorted_vals[idx], 2)


def main() -> int:
    # Import inside main so this module can be imported (e.g., by pytest
    # for type-checking) without pulling the heavy dependency tree.
    from cama.librarian.cama_librarian import retrieve

    # Warm-up: prime caches, schema init, etc.
    try:
        retrieve("warmup", max_librarians=5)
    except Exception as e:
        print(f"[benchmark_retrieval_latency] warmup failed: {e}")
        return 1

    timings_ms: list[float] = []
    librarian_counts: list[int] = []
    memory_counts: list[int] = []

    for query in QUERIES:
        for _ in range(TRIALS_PER_QUERY):
            t0 = time.perf_counter()
            result = retrieve(query, max_librarians=5)
            t1 = time.perf_counter()
            timings_ms.append((t1 - t0) * 1000.0)
            librarian_counts.append(len(result.get("librarians_activated", [])))
            memory_counts.append(len(result.get("memories", [])))

    # Corpus size for context (count only, no row content)
    db_path = os.path.expanduser("~/.cama/memory.db")
    c = sqlite3.connect(db_path)
    try:
        corpus_size = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        durable_size = c.execute(
            "SELECT COUNT(*) FROM memories WHERE status='durable'"
        ).fetchone()[0]
    finally:
        c.close()

    timings_sorted = sorted(timings_ms)
    n = len(timings_sorted)

    summary: dict = {
        "benchmark": "retrieval_latency",
        "version": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "total_memories": corpus_size,
            "durable_memories": durable_size,
        },
        "method": {
            "function_under_test": "cama.librarian.cama_librarian.retrieve",
            "max_librarians_per_query": 5,
            "n_distinct_queries": len(QUERIES),
            "trials_per_query": TRIALS_PER_QUERY,
            "total_runs": n,
            "queries_listed_in_source": True,
            "warmup_call_excluded": True,
        },
        "latency_ms": {
            "p50": _percentile(timings_sorted, 0.50),
            "p90": _percentile(timings_sorted, 0.90),
            "p95": _percentile(timings_sorted, 0.95),
            "p99": _percentile(timings_sorted, 0.99),
            "mean": round(statistics.mean(timings_ms), 2),
            "min": round(min(timings_ms), 2),
            "max": round(max(timings_ms), 2),
            "stdev": round(statistics.stdev(timings_ms), 2) if n > 1 else 0.0,
        },
        "librarians_activated_per_query": {
            "mean": round(statistics.mean(librarian_counts), 2),
            "max": max(librarian_counts),
        },
        "memories_returned_per_query": {
            "mean": round(statistics.mean(memory_counts), 2),
            "max": max(memory_counts),
        },
        "notes": (
            "Wall-clock latency of the Phase-1 librarian retrieval path "
            "(keyword-based routing + per-librarian SQL fan-out). Does not "
            "include the semantic-embedding routing variants (Phase 2.x, "
            "registered separately as cama_lib_route_v2 / v3 / v4), those "
            "warrant their own benchmark because they have a different "
            "cost profile (sentence-transformer embedding compute) and "
            "different correctness contract."
        ),
    }

    print(json.dumps(summary, indent=2))

    repo_root = Path(__file__).resolve().parent.parent.parent
    out = repo_root / "benchmarks" / "benchmark_retrieval_latency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[wrote] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
