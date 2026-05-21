"""Public latency benchmark for the Phase 2.6 semantic routing path.

Companion to ``benchmark_retrieval_latency.py`` (which measures Phase-1
keyword routing + fan-out). This file measures the **semantic** routing
stage on its own — the era-aware gated hybrid (``cama_lib_route_v4``)
that won the April 2026 internal benchmark.

The two costs are not directly subtractable because:
  - Phase-1 benchmark = routing + per-librarian SQL fan-out + dedupe.
  - This benchmark    = semantic routing only (single-centroid +
                        gated era sub-centroid scoring), which is what
                        a semantic-search user pays *before* any
                        fan-out / blending happens.

So the two numbers together let a reader reason about which stage
costs what when comparing the keyword vs semantic retrieval paths.

By design, the output JSON contains **only timing percentiles** — no
query content, no librarian IDs, no result text. The same 20 generic
topic queries used in the keyword-path benchmark are reused so the
methodology stays consistent.

Run:
    python -m cama.eval.benchmark_routing_semantic_latency

Output:
    benchmarks/benchmark_routing_semantic_latency.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Same query set as the Phase-1 latency benchmark, so the two files are
# directly comparable on identical input.
QUERIES: tuple[str, ...] = (
    "memory", "safety", "research", "code", "design",
    "fear", "joy", "morning", "loss", "trust",
    "memory architecture", "research design", "code quality",
    "fear response", "emotional landscape",
    "what i remember", "how do i", "what works for",
    "the right way", "the next step",
)

TRIALS_PER_QUERY: int = 5


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = min(int(n * p), n - 1)
    return round(sorted_vals[idx], 2)


async def _time_one_call(query: str, route_fn) -> float:
    t0 = time.perf_counter()
    await route_fn(query, top_k=5)
    return (time.perf_counter() - t0) * 1000.0


async def _run() -> dict:
    # Ensure cama_mcp is importable so era_subcentroid_route can resolve
    # _get_embedding. The repo root is normally sys.path[0] when this
    # script is invoked via `python -m`; this guard is defensive.
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from cama.librarian.cama_phase26_era_hybrid import era_subcentroid_route

    # Warm-up: loads the sentence-transformer model, primes the SQLite
    # page cache for the librarian centroid rows, and triggers any
    # one-time embedding-cache fills. We measure the warm-up cost
    # separately so reviewers can see model-load overhead.
    t_warm_start = time.perf_counter()
    try:
        await era_subcentroid_route("warmup", top_k=5)
    except Exception as e:
        print(f"[benchmark_routing_semantic_latency] warmup failed: {e}", file=sys.stderr)
        raise
    warmup_ms = (time.perf_counter() - t_warm_start) * 1000.0

    timings_ms: list[float] = []
    for query in QUERIES:
        for _ in range(TRIALS_PER_QUERY):
            timings_ms.append(await _time_one_call(query, era_subcentroid_route))

    db_path = os.path.expanduser("~/.cama/memory.db")
    c = sqlite3.connect(db_path)
    try:
        corpus_size = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        durable_size = c.execute(
            "SELECT COUNT(*) FROM memories WHERE status='durable'"
        ).fetchone()[0]
        librarians = c.execute("SELECT COUNT(*) FROM librarians").fetchone()[0]
        with_centroid = c.execute(
            "SELECT COUNT(*) FROM librarians WHERE centroid_embedding IS NOT NULL"
        ).fetchone()[0]
        era_subs = c.execute(
            "SELECT COUNT(*) FROM librarian_era_subcentroids"
        ).fetchone()[0]
    finally:
        c.close()

    timings_sorted = sorted(timings_ms)
    n = len(timings_sorted)

    return {
        "benchmark": "routing_semantic_latency",
        "version": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "total_memories": corpus_size,
            "durable_memories": durable_size,
            "librarians_with_centroid": with_centroid,
            "librarians_total": librarians,
            "era_subcentroids": era_subs,
        },
        "method": {
            "function_under_test": "cama.librarian.cama_phase26_era_hybrid.era_subcentroid_route",
            "alias": "cama_lib_route_v4",
            "top_k_per_query": 5,
            "n_distinct_queries": len(QUERIES),
            "trials_per_query": TRIALS_PER_QUERY,
            "total_runs": n,
            "queries_listed_in_source": True,
            "warmup_call_excluded_from_percentiles": True,
            "warmup_includes": "sentence-transformer model load, SQLite page-cache prime, first-call embedding cache fill",
        },
        "warmup_ms": round(warmup_ms, 2),
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
        "notes": (
            "Wall-clock latency of the Phase-2.6 era-aware gated hybrid routing "
            "stage alone — query embedding + single-centroid cosine across all "
            "librarians + gated era-subcentroid boost + ranking. Does NOT include "
            "subsequent per-librarian SQL fan-out or blended-scoring stages. "
            "Reported warmup_ms covers sentence-transformer (all-MiniLM-L6-v2) "
            "model load on first call; subsequent embedding calls are warm. "
            "Compare with benchmark_retrieval_latency.json (Phase-1 keyword "
            "route + fan-out, no embedding compute)."
        ),
    }


def main() -> int:
    summary = asyncio.run(_run())
    print(json.dumps(summary, indent=2))
    repo_root = Path(__file__).resolve().parent.parent.parent
    out = repo_root / "benchmarks" / "benchmark_routing_semantic_latency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[wrote] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
