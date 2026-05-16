#!/usr/bin/env python3
"""Backfill today's session into the research journal."""
import sys
sys.path.insert(0, r'C:\Users\User\Desktop\cama')
from research_journal import (
    log_session_start, log_diagnostic, log_finding, 
    log_code_change, log_entry, get_stats
)

# ============================================================
# Session start
# ============================================================
log_session_start(
    "Sleep daemon deep dive + CAMA infrastructure fixes",
    "Angela asked for a rundown on the sleep daemon. Diagnosed all six phases, "
    "found multiple systemic issues, built fixes. Goals: fix recency scoring, "
    "improve edge quality, get the daemon actually working."
)

# ============================================================
# Diagnostics
# ============================================================
log_diagnostic("Initial system state", {
    "durable_memories": 47294,
    "expired_memories": 5174,
    "provisional_memories": 178,
    "total_edges": 1043,
    "edge_types": "resonates:831, resonance:206, elaboration:3, deepens:2, identity:1",
    "provisionals_without_ttl": 173,
    "embeddings_coverage": "100%",
    "affect_coverage": "100%",
    "sleep_daemon_cycles_total": 11,
}, tags=["baseline", "sleep_daemon"])

log_diagnostic("rel_degree distribution (pre-fix)", {
    "rel_degree_0": 47138,
    "rel_degree_gt0": 156,
    "percentage_with_edges": "0.3%",
    "max_rel_degree": 8,
}, tags=["relational", "baseline"])

log_diagnostic("Consolidation window analysis", {
    "window_size": 1000,
    "total_durable": 47294,
    "coverage_pct": 2.1,
    "window_oldest_date": "2026-02-22",
    "window_newest_date": "2026-03-28",
    "conclusion": "99.8% of database never consolidated"
}, tags=["sleep_daemon", "consolidation"])

log_diagnostic("Edge creation simulation - batch 0", {
    "total_comparisons": 6341,
    "blocked_same_day": "6319 (99.7%)",
    "blocked_already_exists": "22 (0.3%)",
    "blocked_too_distant": 0,
    "would_create": 0,
    "root_cause": "Sequential neighbors within emotion clusters are almost always same-day"
}, tags=["sleep_daemon", "consolidation", "critical"])

log_diagnostic("Emotional distance distribution", {
    "median_distance": 0.4082,
    "25th_pct": 0.0,
    "75th_pct": 0.473,
    "under_0.3": 52,
    "under_0.4": 84,
    "under_0.5": 137,
}, tags=["affect", "consolidation"])

log_diagnostic("Duplicate analysis", {
    "recurring_flagged_pairs": 14,
    "example_junk": "ChatGPT 'Processing image...' system messages",
    "example_fragments": "Empty '} PS C:\\Users\\User...' console output",
    "example_real_dupes": "Identity/breakthrough memories imported twice",
    "daemon_behavior": "Logs same dupes every 30min, takes no action",
}, tags=["duplicates", "bulk_import"])

log_diagnostic("Dream phase analysis", {
    "total_dreams_ever": 0,
    "failure_reason": "Requires 3+ durable memories created today, but live memories start as provisional",
    "secondary_reason": "Bulk import dates are historical, not today",
}, tags=["sleep_daemon", "dream"])

# ============================================================
# Findings
# ============================================================
log_finding(
    "99.7% of consolidation comparisons blocked by same-day filter",
    "The sleep daemon's consolidation phase compares sequential neighbors within "
    "emotion clusters. Because bulk import preserved chronological ordering, "
    "neighbors by ID are almost always from the same conversation (same day). "
    "The 1-day temporal minimum distance filter rejects all of them.",
    evidence="6,319 of 6,341 comparisons (99.7%) blocked by same_day filter in batch 0 simulation",
    tags=["critical", "consolidation", "architecture"]
)

log_finding(
    "trust emotion dominates ~50% of bulk import memories",
    "The import_auto affect model defaulted to trust:0.3 for a huge portion "
    "of imported memories. This creates a massive trust cluster that connects "
    "semantically unrelated memories purely on shared affect tags.",
    evidence="479 of 1000 memories in batch 0 have trust as dominant emotion",
    tags=["affect", "bulk_import", "data_quality"]
)

log_finding(
    "Recency scoring broken by Z-suffix timestamps",
    "_parse_t uses datetime.fromisoformat() which fails on Z-suffix timestamps "
    "in Python 3.10. The bare except returns datetime.now(), making recency "
    "score ~1.0 for all affected memories. Estimated ~80% of database affected.",
    evidence="Known Python 3.10 limitation. Bulk import timestamps end in Z.",
    tags=["critical", "recency", "retrieval"]
)

log_finding(
    "rel_degree effectively dead - 99.7% of memories at zero",
    "Only 156 of 47,294 durable memories had any relational edges. "
    "The relational scoring channel (15% of blended retrieval weight) "
    "contributed nothing to retrieval quality.",
    evidence="47,138 memories with rel_degree=0 vs 156 with any edges",
    tags=["critical", "relational", "retrieval"]
)

log_finding(
    "Sleep daemon dream phase has never fired",
    "Zero dream entries in the database across 11 cycles. The threshold "
    "requires 3+ durable memories created today, but live conversation "
    "memories start as provisional and bulk import dates are historical.",
    evidence="0 entries in memories WHERE memory_type='dream'",
    tags=["sleep_daemon", "dream"]
)

log_finding(
    "Cross-temporal sampling solves consolidation bottleneck",
    "Replacing sequential neighbor comparison with random cross-temporal "
    "candidate sampling from the full database produced 200 edges in a "
    "single 5-second cycle. Previous approach: 161 edges in 11 cycles.",
    evidence="v2.1 first cycle: 231 comparisons, 200 edges created, 86.6% hit rate",
    tags=["breakthrough", "consolidation", "sleep_daemon"]
)

# ============================================================
# Code changes
# ============================================================
log_code_change(
    "fix_tier1.py",
    "Tier 1 infrastructure fixes",
    "Three one-time fixes: (1) Recomputed rel_degree from existing 1,043 edges — "
    "updated 379 memories, max degree now 17. (2) Set 14-day review_after TTL on "
    "173 orphaned provisionals. (3) Expired 1,770 text duplicates and 1 junk pattern "
    "from bulk import.",
    tags=["fix", "rel_degree", "provisionals", "duplicates"]
)

log_code_change(
    "cama_sleep_v2.py (v2.0)",
    "Sleep daemon v2.0 — rolling window",
    "Added rolling consolidation cursor stored in aelen_state. Each cycle processes "
    "next 1000 memories by ID, wraps at end. Also: loosened emotional distance "
    "threshold from 0.3 to 0.4, increased neighbor depth from 5 to 8, auto-expire "
    "duplicates instead of just logging, dream phase includes provisionals.",
    code_diff="LIMIT 1000 ORDER BY created_at DESC → LIMIT 1000 OFFSET cursor ORDER BY id ASC",
    tags=["sleep_daemon", "consolidation"]
)

log_code_change(
    "cama_sleep_v2.py (v2.1)",
    "Sleep daemon v2.1 — cross-temporal resonance",
    "Complete rearchitecture of consolidation phase. Instead of comparing sequential "
    "neighbors (99.7% same-day), now samples random candidates from FULL database "
    "sharing the same dominant emotion but from different dates. Anchor memories from "
    "rolling window, candidates from anywhere. Added MAX_EDGES_PER_CYCLE=200 safety cap. "
    "Result: 200 edges in first cycle vs 0 in v2.0 and 161 total across 11 v1 cycles.",
    code_diff="Sequential comparison → cross-temporal random sampling with ORDER BY RANDOM()",
    tags=["sleep_daemon", "consolidation", "breakthrough"]
)

log_code_change(
    "fix_parse_t.py",
    "Fix _parse_t Z-suffix timestamp parsing",
    "Patches _parse_t in cama_mcp.py and cama_sleep_v2.py to handle Z-suffix "
    "timestamps. Replaces trailing 'Z' with '+00:00' before fromisoformat(). "
    "This fixes recency scoring for ~80% of the database.",
    code_diff="datetime.fromisoformat(t) → t.replace('Z', '+00:00'); datetime.fromisoformat(t)",
    tags=["fix", "recency", "critical"]
)

log_code_change(
    "fix_edge_quality.py",
    "Add semantic similarity gate to sleep daemon edges",
    "Before creating an edge, now checks embedding cosine similarity between "
    "anchor and candidate. Requires cosine_sim >= 0.25 (weak but real content "
    "overlap). Edge weight blends affect distance and semantic similarity. "
    "Prevents mass-connecting the trust cluster on affect alone.",
    code_diff="if dist < threshold: create_edge → if dist < threshold AND cosine_sim >= 0.25: create_edge",
    tags=["sleep_daemon", "edge_quality", "embeddings"]
)

log_entry(
    "Research journal system created",
    "Built research_journal.py with SQLite-backed persistent logging. "
    "Supports findings, diagnostics, code changes, session tracking. "
    "CLI for viewing/exporting. Importable API for use from other scripts.",
    entry_type="note",
    tags=["infrastructure", "tooling"]
)

# ============================================================
# Print summary
# ============================================================
stats = get_stats()
print("=== Research Journal Backfill Complete ===")
print(f"  Total entries: {stats['total_entries']}")
for t, c in stats['by_type'].items():
    print(f"    {t}: {c}")
