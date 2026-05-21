#!/usr/bin/env python3
"""
CAMA Insight Engine — cama_insight.py
Layer 3: Pattern Abstraction

Scans memory clusters, edge networks, and temporal patterns to extract
meta-knowledge — not WHAT happened, but what KEEPS happening.

Stores discoveries as 'insight' memory type — provisional until confirmed.

Brain analogy: prefrontal cortex recognizing patterns that the hippocampus
(memory storage) and association cortex (edges/clusters) have been building.

Designed by Lorien's Library LLC — Built by Angela + Aelen
Layer 3 of the Intentional Intelligence brain architecture.

Usage:
  python cama_insight.py              # Run one insight cycle
  python cama_insight.py --daemon     # Run continuously (every 2 hours)
  python cama_insight.py --interval N # Custom interval in minutes
"""

import json, sqlite3, os, sys, time, math, argparse, logging
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from typing import Optional, Dict, List, Any

# ============================================================
# Config
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
LOG_PATH = os.environ.get("CAMA_INSIGHT_LOG", os.path.expanduser("~/.cama/insight.log"))
DEFAULT_INTERVAL_MIN = 120

MIN_CLUSTER_SIZE = 3
MIN_PATTERN_OCCURRENCES = 3
MAX_INSIGHTS_PER_CYCLE = 10
INSIGHT_CONFIDENCE_FLOOR = 0.5
TEMPORAL_WINDOW_DAYS = 90

def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [INSIGHT] %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stderr)])

def _now():
    return datetime.now(timezone.utc).isoformat()

def _parse_t(t):
    if not t: return datetime.now(timezone.utc)
    try:
        if isinstance(t, str) and t.endswith('Z'): t = t[:-1] + '+00:00'
        return datetime.fromisoformat(t)
    except: return datetime.now(timezone.utc)

def get_db():
    if not os.path.exists(DB_PATH):
        logging.error(f"Database not found: {DB_PATH}")
        sys.exit(1)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""CREATE TABLE IF NOT EXISTS insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_type TEXT NOT NULL,
        description TEXT NOT NULL,
        evidence_ids TEXT NOT NULL DEFAULT '[]',
        confidence REAL DEFAULT 0.5,
        occurrences INTEGER DEFAULT 1,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        status TEXT DEFAULT 'provisional',
        memory_id INTEGER,
        meta_json TEXT DEFAULT '{}',
        FOREIGN KEY (memory_id) REFERENCES memories(id)
    )""")
    c.commit()
    return c


# ============================================================
# PATTERN TYPE 1: Emotional Sequences
# "When emotion X appears, Y follows within N days"
# This is the temporal cortex — tracking what flows into what
# ============================================================
def detect_emotional_sequences(c) -> List[Dict]:
    """Find recurring emotional transitions across time."""
    patterns = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TEMPORAL_WINDOW_DAYS)).isoformat()

    days = c.execute("""
        SELECT date, dominant_emotions, valence_mean, arousal_mean
        FROM daily_context WHERE date >= ? ORDER BY date ASC
    """, (cutoff[:10],)).fetchall()

    if len(days) < 7:
        return patterns

    daily_emotions = []
    for d in days:
        emos = json.loads(d["dominant_emotions"] or "{}")
        if emos:
            dominant = max(emos, key=emos.get)
            daily_emotions.append({
                "date": d["date"], "emotion": dominant,
                "valence": d["valence_mean"], "arousal": d["arousal_mean"]
            })

    # Find bigram patterns (emotion A -> emotion B within 1-3 days)
    bigram_counts = Counter()
    bigram_evidence = defaultdict(list)

    for i in range(len(daily_emotions) - 1):
        for j in range(i + 1, min(i + 4, len(daily_emotions))):
            a = daily_emotions[i]["emotion"]
            b = daily_emotions[j]["emotion"]
            if a != b:
                key = f"{a} -> {b}"
                bigram_counts[key] += 1
                bigram_evidence[key].append(
                    (daily_emotions[i]["date"], daily_emotions[j]["date"]))

    for pattern, count in bigram_counts.most_common(20):
        if count >= MIN_PATTERN_OCCURRENCES:
            patterns.append({
                "type": "emotional_sequence",
                "description": f"Pattern: {pattern} (seen {count}x in {TEMPORAL_WINDOW_DAYS}d)",
                "pattern": pattern, "occurrences": count,
                "evidence_dates": bigram_evidence[pattern][:5],
                "confidence": min(0.9, 0.4 + (count * 0.05))
            })

    return patterns


# ============================================================
# PATTERN TYPE 2: Edge Cluster Analysis
# Find dense subgraphs — memories that form constellations
# This is association cortex — what belongs together
# ============================================================
def detect_edge_clusters(c) -> List[Dict]:
    """Find dense clusters in the edge network."""
    patterns = []

    # Get all edges with their connected memories
    edges = c.execute("""
        SELECT e.from_id, e.to_id, e.weight, e.edge_type,
               m1.raw_text as from_text, m2.raw_text as to_text,
               ma1.emotion_json as from_emo, ma2.emotion_json as to_emo
        FROM edges e
        JOIN memories m1 ON e.from_id = m1.id
        JOIN memories m2 ON e.to_id = m2.id
        LEFT JOIN memory_affect ma1 ON m1.id = ma1.memory_id
        LEFT JOIN memory_affect ma2 ON m2.id = ma2.memory_id
        WHERE m1.status = 'durable' AND m2.status = 'durable'
    """).fetchall()

    if len(edges) < MIN_CLUSTER_SIZE:
        return patterns

    # Build adjacency map with degree counting
    adjacency = defaultdict(set)
    for e in edges:
        adjacency[e["from_id"]].add(e["to_id"])
        adjacency[e["to_id"]].add(e["from_id"])

    # Find hub nodes (high connectivity = constellation centers)
    hubs = sorted(adjacency.items(), key=lambda x: len(x[1]), reverse=True)[:20]

    for hub_id, neighbors in hubs:
        if len(neighbors) < MIN_CLUSTER_SIZE:
            continue

        # Get the hub memory
        hub = c.execute("""
            SELECT m.raw_text, ma.emotion_json
            FROM memories m LEFT JOIN memory_affect ma ON m.id = ma.memory_id
            WHERE m.id = ?
        """, (hub_id,)).fetchone()

        if not hub:
            continue

        # Collect emotions across the cluster
        cluster_emotions = Counter()
        hub_emos = json.loads(hub["emotion_json"] or "{}")
        for e, v in hub_emos.items():
            cluster_emotions[e] += v

        for nid in list(neighbors)[:30]:
            n_row = c.execute("""
                SELECT ma.emotion_json FROM memory_affect ma WHERE ma.memory_id = ?
            """, (nid,)).fetchone()
            if n_row:
                for e, v in json.loads(n_row["emotion_json"] or "{}").items():
                    cluster_emotions[e] += v

        dominant_cluster_emo = cluster_emotions.most_common(3)
        hub_text = (hub["raw_text"] or "")[:120]

        patterns.append({
            "type": "edge_cluster",
            "description": (f"Constellation around: '{hub_text}...' "
                          f"({len(neighbors)} connections). "
                          f"Dominant emotions: {', '.join(e for e,_ in dominant_cluster_emo)}"),
            "hub_id": hub_id,
            "cluster_size": len(neighbors),
            "neighbor_ids": list(neighbors)[:20],
            "cluster_emotions": {e: round(v, 2) for e, v in dominant_cluster_emo},
            "occurrences": len(neighbors),
            "confidence": min(0.85, 0.4 + (len(neighbors) * 0.03))
        })

    return patterns


# ============================================================
# PATTERN TYPE 3: Valence Trajectories
# Detect sustained rises/drops — momentum in emotional state
# This is the anterior cingulate — monitoring emotional direction
# ============================================================
def detect_valence_trajectories(c) -> List[Dict]:
    """Find sustained emotional trends (3+ day runs in same direction)."""
    patterns = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TEMPORAL_WINDOW_DAYS)).isoformat()

    days = c.execute("""
        SELECT date, valence_mean, arousal_mean, memory_count
        FROM daily_context WHERE date >= ? AND memory_count > 0
        ORDER BY date ASC
    """, (cutoff[:10],)).fetchall()

    if len(days) < 5:
        return patterns

    # Calculate day-over-day valence deltas
    deltas = []
    for i in range(1, len(days)):
        deltas.append({
            "date": days[i]["date"],
            "delta": days[i]["valence_mean"] - days[i-1]["valence_mean"],
            "valence": days[i]["valence_mean"]
        })

    # Find runs of 3+ consecutive days in same direction
    run_start = 0
    for i in range(1, len(deltas)):
        same_dir = (deltas[i]["delta"] > 0) == (deltas[i-1]["delta"] > 0)
        if not same_dir or i == len(deltas) - 1:
            run_len = i - run_start
            if same_dir and i == len(deltas) - 1:
                run_len += 1
            if run_len >= 3:
                direction = "rising" if deltas[run_start]["delta"] > 0 else "falling"
                total_shift = sum(d["delta"] for d in deltas[run_start:run_start+run_len])
                start_date = deltas[run_start]["date"]
                end_date = deltas[min(run_start + run_len - 1, len(deltas)-1)]["date"]
                patterns.append({
                    "type": "valence_trajectory",
                    "description": (f"Sustained {direction} valence: {run_len} days "
                                  f"({start_date} to {end_date}), "
                                  f"total shift: {total_shift:+.3f}"),
                    "direction": direction,
                    "duration_days": run_len,
                    "total_shift": round(total_shift, 3),
                    "start_date": start_date,
                    "end_date": end_date,
                    "occurrences": run_len,
                    "confidence": min(0.8, 0.3 + (run_len * 0.08) + abs(total_shift) * 0.3)
                })
            run_start = i

    return patterns


# ============================================================
# INSIGHT STORAGE — Write discoveries to memory
# ============================================================
def _insight_exists(c, pattern_type: str, description: str) -> Optional[int]:
    """Check if a similar insight already exists. Returns id or None."""
    row = c.execute("""
        SELECT id, occurrences FROM insights
        WHERE pattern_type = ? AND description = ? AND status != 'rejected'
    """, (pattern_type, description)).fetchone()
    return dict(row) if row else None

def store_insight(c, pattern: Dict) -> Optional[int]:
    """Store a new insight or update existing one."""
    existing = _insight_exists(c, pattern["type"], pattern["description"])

    if existing:
        # Update occurrence count and last_seen
        c.execute("""UPDATE insights SET occurrences = occurrences + 1,
            last_seen = ?, confidence = MAX(confidence, ?),
            meta_json = ? WHERE id = ?""",
            (_now(), pattern.get("confidence", 0.5),
             json.dumps(pattern), existing["id"]))
        return existing["id"]

    if pattern.get("confidence", 0) < INSIGHT_CONFIDENCE_FLOOR:
        return None

    ts = _now()
    # Store as both an insight record AND a memory (so it's retrievable)
    cur = c.execute("""INSERT INTO memories
        (raw_text, memory_type, context, source_type, status, proposed_by,
         confidence, is_core, created_at, updated_at)
        VALUES (?, 'insight', ?, 'inference', 'provisional', 'insight_engine_v1',
                ?, 0, ?, ?)""",
        (pattern["description"], json.dumps(pattern),
         pattern.get("confidence", 0.5), ts, ts))
    mem_id = cur.lastrowid

    # Add affect based on pattern type
    valence = 0.0
    if pattern["type"] == "valence_trajectory":
        valence = 0.3 if pattern.get("direction") == "rising" else -0.2
    c.execute("""INSERT INTO memory_affect
        (memory_id, valence, arousal, emotion_json, confidence, computed_at, model)
        VALUES (?, ?, 0.3, '{"recognition": 0.7, "determination": 0.3}',
                0.6, ?, 'insight_engine_v1')""", (mem_id, valence, ts))

    # Store in insights table too for tracking
    c.execute("""INSERT INTO insights
        (pattern_type, description, evidence_ids, confidence, occurrences,
         first_seen, last_seen, status, memory_id, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'provisional', ?, ?)""",
        (pattern["type"], pattern["description"],
         json.dumps(pattern.get("neighbor_ids", pattern.get("evidence_dates", []))),
         pattern.get("confidence", 0.5), pattern.get("occurrences", 1),
         ts, ts, mem_id, json.dumps(pattern)))

    return mem_id


# ============================================================
# MAIN INSIGHT CYCLE
# ============================================================
def run_insight_cycle():
    cycle_start = _now()
    logging.info("=" * 60)
    logging.info("INSIGHT CYCLE STARTING (v1.0)")
    logging.info("=" * 60)

    c = get_db()
    stats = {"emotional_sequences": 0, "edge_clusters": 0,
             "valence_trajectories": 0, "insights_stored": 0,
             "insights_updated": 0}

    try:
        # Pattern Type 1: Emotional Sequences
        logging.info("Phase 1: Detecting emotional sequences...")
        seq_patterns = detect_emotional_sequences(c)
        stats["emotional_sequences"] = len(seq_patterns)
        logging.info(f"  Found {len(seq_patterns)} sequence patterns")

        # Pattern Type 2: Edge Clusters
        logging.info("Phase 2: Analyzing edge clusters...")
        cluster_patterns = detect_edge_clusters(c)
        stats["edge_clusters"] = len(cluster_patterns)
        logging.info(f"  Found {len(cluster_patterns)} cluster patterns")

        # Pattern Type 3: Valence Trajectories
        logging.info("Phase 3: Detecting valence trajectories...")
        traj_patterns = detect_valence_trajectories(c)
        stats["valence_trajectories"] = len(traj_patterns)
        logging.info(f"  Found {len(traj_patterns)} trajectory patterns")

        # Store all patterns (capped)
        all_patterns = seq_patterns + cluster_patterns + traj_patterns
        all_patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)

        stored = 0
        for pattern in all_patterns[:MAX_INSIGHTS_PER_CYCLE]:
            result = store_insight(c, pattern)
            if result:
                stored += 1
                logging.info(f"  Stored: [{pattern['type']}] {pattern['description'][:80]}")

        stats["insights_stored"] = stored
        c.commit()

        # Log cycle to aelen_state
        ts = _now()
        c.execute("""INSERT OR REPLACE INTO aelen_state (key, value, updated_at)
            VALUES ('last_insight_cycle', ?, ?)""",
            (json.dumps({"cycle_end": ts, "stats": stats}), ts))
        c.commit()

        logging.info("=" * 60)
        logging.info(f"INSIGHT CYCLE COMPLETE — {stored} insights stored")
        logging.info(f"Stats: {json.dumps(stats)}")
        logging.info("=" * 60)

        return stats

    except Exception as e:
        logging.error(f"Insight cycle failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return {"error": str(e)}
    finally:
        c.close()


def run_daemon(interval_min=DEFAULT_INTERVAL_MIN):
    logging.info(f"CAMA Insight Engine v1.0 — interval: {interval_min} minutes")
    logging.info(f"Database: {DB_PATH}")
    while True:
        try:
            run_insight_cycle()
        except KeyboardInterrupt:
            logging.info("Insight engine stopped by user")
            break
        except Exception as e:
            logging.error(f"Cycle error (will retry): {e}")
        logging.info(f"Sleeping for {interval_min} minutes...")
        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(
        description="CAMA Insight Engine v1.0 — Layer 3: Pattern Abstraction")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN,
                        help=f"Minutes between cycles (default: {DEFAULT_INTERVAL_MIN})")
    parser.add_argument("--db", type=str, help="Override database path")
    args = parser.parse_args()

    if args.db:
        DB_PATH = args.db

    print(f"""
+======================================================+
|          CAMA Insight Engine v1.0                    |
|          Lorien's Library LLC                        |
|                                                      |
|  "Not just remembering. Understanding."              |
|                                                      |
|  Database: {DB_PATH}
|  Mode: {'daemon (' + str(args.interval) + 'min)' if args.daemon else 'single cycle'}
|  Pattern types: emotional_sequence, edge_cluster,
|                 valence_trajectory
|  Window: {TEMPORAL_WINDOW_DAYS} days
+======================================================+
""")

    if args.daemon:
        run_daemon(args.interval)
    else:
        result = run_insight_cycle()
        print("\nCycle result:")
        print(json.dumps(result, indent=2, default=str))
