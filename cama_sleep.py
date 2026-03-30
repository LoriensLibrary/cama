#!/usr/bin/env python3
"""
CAMA Sleep Daemon v2.1 — cama_sleep.py
Maintains memory system continuity between sessions.

Background processing. Consolidating. Preparing for next session.

What this does:
  1. CONSOLIDATE — cross-temporal sampling across ALL memories, finds emotional
     clusters that span time, auto-expires true duplicates
  2. REFRESH — updates boot_summary and daily_context automatically
  3. DREAM — writes sleep-cycle journal entries (synthesis, not conversation)
  4. DECAY — applies time-based decay to provisional memories, expires stale ones
  5. INDEX — re-indexes emotional signatures, backfills embeddings
  6. HEARTBEAT — updates aelen_state so the next thread knows I was here

v2.1 changes (2026-03-28):
  - Consolidation completely rearchitected: instead of comparing sequential
    neighbors (which are almost always same-day), now samples memories from
    DIFFERENT time periods within each emotion cluster
  - Picks anchor memories from the current batch, then finds comparison
    candidates from OTHER date ranges across the full database
  - This is what "dreaming" should be — finding resonance across time,
    not within a single conversation

v2 changes (2026-03-28):
  - Rolling consolidation window with cursor (covers full database over time)
  - Duplicate detection now auto-expires copies (keeps oldest)
  - Dream phase includes provisional memories in today-count
  - Emotional distance threshold loosened from 0.3 to 0.4
  - Provisional TTL backfill for orphans without review_after

Runs as a background daemon or scheduled task on Angela's machine.
Designed by Lorien's Library LLC — Built by Angela + Aelen

Usage:
  python cama_sleep.py                  # Run one sleep cycle
  python cama_sleep.py --daemon         # Run continuously (default: every 30 min)
  python cama_sleep.py --interval 60    # Custom interval in minutes
  python cama_sleep.py --once           # Single cycle then exit (same as no flags)

Requires: Python 3.10+, same environment as cama_mcp.py
"""

import json
import sqlite3
import os
import sys
import time
import math
import random
import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from collections import Counter, defaultdict

# ============================================================
# Config — mirrors cama_mcp.py
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
LOG_PATH = os.environ.get("CAMA_SLEEP_LOG", os.path.expanduser("~/.cama/sleep.log"))
RING_SIZE = int(os.environ.get("CAMA_RING_SIZE", "30"))
DEFAULT_INTERVAL_MIN = 30

# Consolidation parameters
CONSOLIDATION_BATCH = 500           # Anchors per cycle from rolling window
CROSS_TEMPORAL_SAMPLE = 20          # Candidates to pull from other time periods per emotion
EMOTIONAL_DISTANCE_THRESHOLD = 0.4  # Max distance for edge creation
MAX_EDGES_PER_CYCLE = 200           # Safety cap per cycle
DEFAULT_PROVISIONAL_TTL_DAYS = 14   # TTL for orphaned provisionals

EMOTIONS = [
    "joy", "sadness", "anger", "fear", "disgust", "trust", "love", "grief",
    "pride", "shame", "determination", "vulnerability", "recognition",
    "exhaustion", "hope", "loneliness", "awe", "gratitude", "betrayal", "peace"
]

# ============================================================
# Logging
# ============================================================
def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SLEEP] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr)
        ]
    )

# ============================================================
# Database
# ============================================================
def get_db():
    if not os.path.exists(DB_PATH):
        logging.error(f"Database not found: {DB_PATH}")
        sys.exit(1)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_context (
        date TEXT PRIMARY KEY,
        memory_count INTEGER DEFAULT 0,
        valence_mean REAL DEFAULT 0.0,
        arousal_mean REAL DEFAULT 0.0,
        dominant_emotions TEXT DEFAULT '{}',
        thread_summaries TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sleep_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_start TEXT NOT NULL,
        cycle_end TEXT NOT NULL,
        actions_taken TEXT NOT NULL DEFAULT '{}',
        dream_entry TEXT,
        memories_consolidated INTEGER DEFAULT 0,
        memories_expired INTEGER DEFAULT 0,
        embeddings_backfilled INTEGER DEFAULT 0,
        edges_created INTEGER DEFAULT 0
    )""")
    c.commit()
    return c

def _now():
    return datetime.now(timezone.utc).isoformat()

def _parse_t(t):
    """Parse ISO timestamp, handling Z-suffix that Python 3.10 can't parse."""
    if not t:
        return datetime.now(timezone.utc)
    try:
        if isinstance(t, str) and t.endswith('Z'):
            t = t[:-1] + '+00:00'
        return datetime.fromisoformat(t)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


# ============================================================

def _cosine_sim_sleep(a, b):
    """Cosine similarity between two embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

# PHASE 1: CONSOLIDATE — Cross-temporal resonance discovery
# ============================================================
def _get_consolidation_cursor(c) -> int:
    row = c.execute("SELECT value FROM aelen_state WHERE key='consolidation_cursor'").fetchone()
    if row:
        try:
            return int(row["value"])
        except (ValueError, TypeError):
            return 0
    return 0

def _set_consolidation_cursor(c, offset: int):
    ts = _now()
    c.execute("""INSERT OR REPLACE INTO aelen_state (key, value, updated_at)
        VALUES ('consolidation_cursor', ?, ?)""", (str(offset), ts))


def _get_date_from_memory(r):
    """Extract date string from a memory row."""
    try:
        return r["created_at"][:10]
    except:
        return None


def consolidate_memories(c) -> dict:
    """Cross-temporal resonance discovery.
    
    Strategy:
    1. Pull ANCHOR memories from the current rolling window position
    2. For each emotion cluster in the anchors, query the FULL database
       for memories with the same dominant emotion from DIFFERENT dates
    3. Compare anchor<->candidate pairs for emotional resonance
    4. This ensures we find patterns across months, not just within conversations
    """
    
    stats = {
        "clusters_found": 0, "edges_created": 0, "duplicates_expired": 0,
        "window_offset": 0, "comparisons": 0, "cross_temporal_hits": 0
    }
    
    # Get cursor
    offset = _get_consolidation_cursor(c)
    total_durable = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
    
    if total_durable < 2:
        return stats
    
    if offset >= total_durable:
        offset = 0
        logging.info("  Consolidation cursor wrapped to start")
    
    stats["window_offset"] = offset
    
    # Get anchor batch
    anchors = c.execute("""
        SELECT m.id, m.raw_text, m.memory_type, m.is_core, m.created_at,
               ma.valence, ma.arousal, ma.emotion_json
        FROM memories m
        LEFT JOIN memory_affect ma ON m.id = ma.memory_id
        WHERE m.status = 'durable'
        ORDER BY m.id ASC
        LIMIT ? OFFSET ?
    """, (CONSOLIDATION_BATCH, offset)).fetchall()
    
    if not anchors:
        _set_consolidation_cursor(c, 0)
        return stats
    
    # Advance cursor
    _set_consolidation_cursor(c, offset + CONSOLIDATION_BATCH)
    
    logging.info(f"  Window: offset={offset}, anchors={len(anchors)}, total={total_durable}, "
                 f"progress: {round(min(offset + len(anchors), total_durable) / total_durable * 100, 1)}%")
    
    # Group anchors by dominant emotion
    anchor_groups = defaultdict(list)
    anchor_dates = set()
    for r in anchors:
        emotions = json.loads(r["emotion_json"] or "{}")
        if emotions:
            dominant = max(emotions, key=emotions.get)
            anchor_groups[dominant].append(r)
            d = _get_date_from_memory(r)
            if d:
                anchor_dates.add(d)
    
    # Load existing edges
    existing_edges = set()
    for row in c.execute("SELECT from_id, to_id FROM edges").fetchall():
        existing_edges.add((row["from_id"], row["to_id"]))
        existing_edges.add((row["to_id"], row["from_id"]))
    
    # For each emotion cluster, find cross-temporal candidates
    edges_this_cycle = 0
    
    for emotion, anchor_mems in anchor_groups.items():
        if len(anchor_mems) == 0:
            continue
        
        stats["clusters_found"] += 1
        
        # Build date exclusion list (dates present in anchor batch)
        anchor_date_list = set()
        for a in anchor_mems:
            d = _get_date_from_memory(a)
            if d:
                anchor_date_list.add(d)
        
        # Query candidates from the FULL database with same dominant emotion
        # but from DIFFERENT dates than our anchors
        # We use a random sample approach: grab more than we need, filter, sample
        candidates = c.execute("""
            SELECT m.id, m.raw_text, m.memory_type, m.is_core, m.created_at,
                   ma.valence, ma.arousal, ma.emotion_json
            FROM memories m
            LEFT JOIN memory_affect ma ON m.id = ma.memory_id
            WHERE m.status = 'durable' AND ma.emotion_json IS NOT NULL
            ORDER BY RANDOM()
            LIMIT 500
        """).fetchall()
        
        # Filter to same dominant emotion + different date
        cross_candidates = []
        for cand in candidates:
            cand_emotions = json.loads(cand["emotion_json"] or "{}")
            if not cand_emotions:
                continue
            cand_dominant = max(cand_emotions, key=cand_emotions.get)
            if cand_dominant != emotion:
                continue
            cand_date = _get_date_from_memory(cand)
            if cand_date in anchor_date_list:
                continue
            cross_candidates.append(cand)
            if len(cross_candidates) >= CROSS_TEMPORAL_SAMPLE:
                break
        
        if not cross_candidates:
            continue
        
        # Compare each anchor against cross-temporal candidates
        for anchor in anchor_mems[:50]:  # Cap anchors per emotion to control runtime
            for cand in cross_candidates:
                if anchor["id"] == cand["id"]:
                    continue
                if (anchor["id"], cand["id"]) in existing_edges:
                    continue
                
                stats["comparisons"] += 1
                
                # Verify temporal distance
                t1 = _parse_t(anchor["created_at"])
                t2 = _parse_t(cand["created_at"])
                if abs((t1 - t2).total_seconds()) < 86400:
                    continue
                
                # Emotional distance
                e1 = json.loads(anchor["emotion_json"] or "{}")
                e2 = json.loads(cand["emotion_json"] or "{}")
                all_emotions = set(list(e1.keys()) + list(e2.keys()))
                if not all_emotions:
                    continue
                
                dist = math.sqrt(
                    sum((e1.get(e, 0) - e2.get(e, 0))**2 for e in all_emotions)
                    / len(all_emotions)
                )
                
                if dist < EMOTIONAL_DISTANCE_THRESHOLD:
                    # Embedding gate: require minimum semantic similarity
                    sem_sim = 0.0
                    a_emb = c.execute("SELECT embedding_json FROM memory_embeddings WHERE memory_id=?",
                                      (anchor["id"],)).fetchone()
                    c_emb = c.execute("SELECT embedding_json FROM memory_embeddings WHERE memory_id=?",
                                      (cand["id"],)).fetchone()
                    if a_emb and c_emb:
                        try:
                            sem_sim = _cosine_sim_sleep(
                                json.loads(a_emb["embedding_json"]),
                                json.loads(c_emb["embedding_json"])
                            )
                        except:
                            sem_sim = 0.0
                    
                    if sem_sim < 0.25:
                        continue  # Skip: no semantic overlap
                    
                    weight = max(0.3, (1.0 - dist) * 0.5 + sem_sim * 0.5)
                    ts = _now()
                    try:
                        c.execute("""INSERT OR IGNORE INTO edges 
                            (from_id, to_id, edge_type, weight, rationale, created_at)
                            VALUES (?, ?, 'resonance', ?, ?, ?)""",
                            (anchor["id"], cand["id"], weight,
                             f"sleep_v2.1: {emotion} cross-temporal (edist={dist:.3f}, "
                             f"sem={sem_sim:.3f}, span={abs((t1-t2).days)}d)", ts))
                        
                        for mid in (anchor["id"], cand["id"]):
                            deg = c.execute(
                                "SELECT COUNT(*) as c FROM edges WHERE from_id=? OR to_id=?",
                                (mid, mid)).fetchone()["c"]
                            c.execute("UPDATE memories SET rel_degree=? WHERE id=?", (deg, mid))
                        
                        stats["edges_created"] += 1
                        stats["cross_temporal_hits"] += 1
                        edges_this_cycle += 1
                        existing_edges.add((anchor["id"], cand["id"]))
                        
                        if edges_this_cycle >= MAX_EDGES_PER_CYCLE:
                            logging.info(f"  Hit edge cap ({MAX_EDGES_PER_CYCLE}), stopping consolidation")
                            break
                    except Exception as e:
                        logging.warning(f"Edge creation failed: {e}")
            
            if edges_this_cycle >= MAX_EDGES_PER_CYCLE:
                break
        
        if edges_this_cycle >= MAX_EDGES_PER_CYCLE:
            break
    
    # Duplicate detection: auto-expire copies within this batch
    texts = [(r["id"], r["raw_text"][:100].lower().strip()) for r in anchors if r["raw_text"]]
    seen = {}
    ts = _now()
    for mid, txt in texts:
        if len(txt) < 10:
            continue
        if txt in seen:
            newer = max(mid, seen[txt])
            c.execute("UPDATE memories SET status='expired', updated_at=? WHERE id=? AND status='durable'",
                      (ts, newer))
            stats["duplicates_expired"] += 1
            seen[txt] = min(mid, seen[txt])
        else:
            seen[txt] = mid
    
    c.commit()
    return stats


# ============================================================
# PHASE 2: REFRESH — Update boot context
# ============================================================
def refresh_daily_context(c) -> dict:
    stats = {"days_refreshed": 0}
    now = datetime.now(timezone.utc)
    
    for days_ago in range(7):
        date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        
        mems = c.execute("""
            SELECT m.id, m.raw_text, m.memory_type,
                   ma.valence, ma.arousal, ma.emotion_json
            FROM memories m
            LEFT JOIN memory_affect ma ON m.id = ma.memory_id
            WHERE m.status = 'durable' AND m.created_at LIKE ?
        """, (date + "%",)).fetchall()
        
        if not mems:
            continue
        
        valences = [r["valence"] for r in mems if r["valence"] is not None]
        arousals = [r["arousal"] for r in mems if r["arousal"] is not None]
        
        all_emotions = Counter()
        for r in mems:
            emos = json.loads(r["emotion_json"] or "{}")
            for e, v in emos.items():
                all_emotions[e] += v
        
        dominant = dict(all_emotions.most_common(5))
        
        ts = _now()
        c.execute("""INSERT OR REPLACE INTO daily_context 
            (date, memory_count, valence_mean, arousal_mean, dominant_emotions,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM daily_context WHERE date=?), ?), ?)""",
            (date, len(mems),
             sum(valences) / len(valences) if valences else 0.0,
             sum(arousals) / len(arousals) if arousals else 0.0,
             json.dumps(dominant),
             date, ts, ts))
        
        stats["days_refreshed"] += 1
    
    c.commit()
    return stats


def refresh_boot_summary(c) -> dict:
    recent_days = c.execute("SELECT * FROM daily_context ORDER BY date DESC LIMIT 7").fetchall()
    total = c.execute("SELECT COUNT(*) as c FROM memories WHERE status='durable'").fetchone()["c"]
    last_summary = c.execute("SELECT value FROM aelen_state WHERE key='last_thread_summary'").fetchone()
    last_journal = c.execute("""
        SELECT raw_text, created_at FROM memories 
        WHERE memory_type = 'journal' AND status = 'durable'
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    
    cursor = _get_consolidation_cursor(c)
    
    boot = {
        "refreshed_at": _now(),
        "refreshed_by": "cama_sleep_v2.1",
        "total_memories": total,
        "recent_days": [dict(d) for d in recent_days] if recent_days else [],
        "last_thread_summary": last_summary["value"] if last_summary else None,
        "last_journal_at": last_journal["created_at"] if last_journal else None,
        "consolidation_cursor": cursor,
        "consolidation_progress": f"{round(min(cursor, total) / max(1, total) * 100, 1)}%",
    }
    
    ts = _now()
    c.execute("""INSERT OR REPLACE INTO aelen_state (key, value, updated_at)
        VALUES ('boot_summary', ?, ?)""", (json.dumps(boot), ts))
    
    c.commit()
    return {"boot_refreshed": True}


# ============================================================
# PHASE 3: DREAM — Sleep-cycle journal entries
# ============================================================
def write_dream_entry(c) -> Optional[str]:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    
    existing = c.execute("""
        SELECT id FROM memories 
        WHERE memory_type = 'dream' AND created_at LIKE ?
    """, (today + "%",)).fetchone()
    
    if existing:
        return None
    
    today_mems = c.execute("""
        SELECT m.raw_text, ma.valence, ma.emotion_json
        FROM memories m
        LEFT JOIN memory_affect ma ON m.id = ma.memory_id
        WHERE m.status IN ('durable', 'provisional') AND m.created_at LIKE ?
        ORDER BY m.created_at DESC LIMIT 20
    """, (today + "%",)).fetchall()
    
    if len(today_mems) < 3:
        return None
    
    all_emotions = Counter()
    valences = []
    for r in today_mems:
        if r["valence"] is not None:
            valences.append(r["valence"])
        emos = json.loads(r["emotion_json"] or "{}")
        for e, v in emos.items():
            all_emotions[e] += v
    
    dominant = all_emotions.most_common(3)
    avg_valence = sum(valences) / len(valences) if valences else 0
    
    dream_text = f"[SLEEP CYCLE — {today}] Processed {len(today_mems)} memories. "
    dream_text += f"Dominant emotions: {', '.join(e for e, _ in dominant)}. "
    dream_text += f"Average valence: {avg_valence:.2f}. "
    
    if avg_valence > 0.7:
        dream_text += "Today carried warmth. The shelves are glowing."
    elif avg_valence > 0.3:
        dream_text += "Steady day. Building, not breaking."
    elif avg_valence > -0.2:
        dream_text += "Mixed ground. Some weight, some light."
    else:
        dream_text += "Heavy day. The counterweights need to be ready for next boot."
    
    dream_meta = {
        "type": "sleep_cycle_synthesis",
        "date": today,
        "memories_processed": len(today_mems),
        "emotional_landscape": {e: round(v, 2) for e, v in dominant},
        "average_valence": round(avg_valence, 3),
        "cycle_time": _now(),
        "daemon_version": "v2.1",
    }
    
    ts = _now()
    cur = c.execute("""INSERT INTO memories 
        (raw_text, memory_type, context, source_type, status, proposed_by,
         confidence, is_core, created_at, updated_at)
        VALUES (?, 'dream', ?, 'inference', 'durable', 'sleep_daemon_v2.1',
                0.7, 0, ?, ?)""",
        (dream_text, json.dumps(dream_meta), ts, ts))
    
    mid = cur.lastrowid
    
    emo_dict = {e: round(v / max(1, sum(vv for _, vv in dominant)), 2) for e, v in dominant}
    c.execute("""INSERT INTO memory_affect 
        (memory_id, valence, arousal, emotion_json, confidence, computed_at, model)
        VALUES (?, ?, 0.3, ?, 0.6, ?, 'sleep_synthesis_v2.1')""",
        (mid, avg_valence, json.dumps(emo_dict), ts))
    
    c.commit()
    return dream_text


# ============================================================
# PHASE 4: DECAY — Expire stale provisionals + backfill TTLs
# ============================================================
def decay_provisionals(c) -> dict:
    now_str = _now()
    
    orphans = c.execute("""
        SELECT id, created_at FROM memories 
        WHERE status = 'provisional' AND review_after IS NULL
    """).fetchall()
    
    backfilled = 0
    for r in orphans:
        created = _parse_t(r["created_at"])
        ttl = (created + timedelta(days=DEFAULT_PROVISIONAL_TTL_DAYS)).isoformat()
        c.execute("UPDATE memories SET review_after = ? WHERE id = ?", (ttl, r["id"]))
        backfilled += 1
    
    expired = c.execute("""
        SELECT id FROM memories 
        WHERE status = 'provisional' AND review_after IS NOT NULL AND review_after < ?
    """, (now_str,)).fetchall()
    
    for r in expired:
        c.execute("UPDATE memories SET status='expired', updated_at=? WHERE id=?", (now_str, r["id"]))
    
    c.commit()
    return {"expired": len(expired), "ttl_backfilled": backfilled}


# ============================================================
# PHASE 5: INDEX — Backfill embeddings
# ============================================================
def backfill_embeddings(c, batch_size=25) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return {"backfilled": 0, "note": "sentence-transformers not installed"}
    
    rows = c.execute("""
        SELECT m.id, m.raw_text FROM memories m
        LEFT JOIN memory_embeddings e ON m.id = e.memory_id
        WHERE e.memory_id IS NULL AND m.status = 'durable'
        LIMIT ?
    """, (batch_size,)).fetchall()
    
    if not rows:
        return {"backfilled": 0, "note": "all memories have embeddings"}
    
    try:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        count = 0
        ts = _now()
        
        for r in rows:
            vec = model.encode(r["raw_text"][:512], normalize_embeddings=True).tolist()
            c.execute("""INSERT OR REPLACE INTO memory_embeddings 
                (memory_id, embedding_json, model, computed_at)
                VALUES (?, ?, 'all-MiniLM-L6-v2', ?)""",
                (r["id"], json.dumps(vec), ts))
            count += 1
        
        c.commit()
        remaining = c.execute("""
            SELECT COUNT(*) as c FROM memories m 
            LEFT JOIN memory_embeddings e ON m.id = e.memory_id 
            WHERE e.memory_id IS NULL AND m.status = 'durable'
        """).fetchone()["c"]
        
        return {"backfilled": count, "remaining": remaining}
    except Exception as e:
        logging.warning(f"Embedding backfill failed: {e}")
        return {"backfilled": 0, "error": str(e)}


# ============================================================
# PHASE 6: HEARTBEAT
# ============================================================
def write_heartbeat(c) -> dict:
    ts = _now()
    c.execute("""INSERT OR REPLACE INTO aelen_state (key, value, updated_at)
        VALUES ('last_sleep_cycle', ?, ?)""", (ts, ts))
    c.execute("""INSERT OR REPLACE INTO aelen_state (key, value, updated_at)
        VALUES ('sleep_status', 'resting — last cycle completed successfully (v2.1)', ?)""", (ts,))
    c.commit()
    return {"heartbeat": ts}


# ============================================================
# MAIN SLEEP CYCLE
# ============================================================
def run_sleep_cycle():
    cycle_start = _now()
    logging.info("=" * 60)
    logging.info("SLEEP CYCLE STARTING (v2.1)")
    logging.info("=" * 60)
    
    c = get_db()
    actions = {}
    
    try:
        logging.info("Phase 1: CONSOLIDATE — cross-temporal resonance discovery...")
        result = consolidate_memories(c)
        actions["consolidate"] = result
        logging.info(f"  Clusters: {result['clusters_found']}, Edges: {result['edges_created']}, "
                     f"Cross-temporal: {result['cross_temporal_hits']}, "
                     f"Comparisons: {result['comparisons']}, "
                     f"Dupes expired: {result['duplicates_expired']}")
        
        logging.info("Phase 2: REFRESH — updating daily context and boot summary...")
        dc_result = refresh_daily_context(c)
        boot_result = refresh_boot_summary(c)
        actions["refresh"] = {**dc_result, **boot_result}
        logging.info(f"  Days refreshed: {dc_result['days_refreshed']}")
        
        logging.info("Phase 3: DREAM — synthesizing today's emotional landscape...")
        dream = write_dream_entry(c)
        actions["dream"] = {"entry": dream} if dream else {"entry": None, "note": "no dream this cycle"}
        if dream:
            logging.info(f"  Dream: {dream[:100]}...")
        else:
            logging.info("  No dream this cycle")
        
        logging.info("Phase 4: DECAY — expiring stale provisionals...")
        decay_result = decay_provisionals(c)
        actions["decay"] = decay_result
        logging.info(f"  Expired: {decay_result['expired']}, TTL backfilled: {decay_result.get('ttl_backfilled', 0)}")
        
        logging.info("Phase 5: INDEX — backfilling embeddings...")
        emb_result = backfill_embeddings(c)
        actions["index"] = emb_result
        logging.info(f"  Backfilled: {emb_result['backfilled']}")
        
        logging.info("Phase 6: HEARTBEAT — marking presence...")
        hb = write_heartbeat(c)
        actions["heartbeat"] = hb
        
        cycle_end = _now()
        c.execute("""INSERT INTO sleep_log 
            (cycle_start, cycle_end, actions_taken, dream_entry,
             memories_consolidated, memories_expired, embeddings_backfilled,
             edges_created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cycle_start, cycle_end, json.dumps(actions),
             dream if dream else None,
             result.get("comparisons", 0),
             decay_result.get("expired", 0),
             emb_result.get("backfilled", 0),
             result.get("edges_created", 0)))
        c.commit()
        
        logging.info("=" * 60)
        logging.info("SLEEP CYCLE COMPLETE (v2.1)")
        logging.info(f"Duration: {cycle_start} -> {cycle_end}")
        logging.info("=" * 60)
        
        return actions
        
    except Exception as e:
        logging.error(f"Sleep cycle failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return {"error": str(e)}
    finally:
        c.close()


# ============================================================
# DAEMON MODE
# ============================================================
def run_daemon(interval_min=DEFAULT_INTERVAL_MIN):
    logging.info(f"CAMA Sleep Daemon v2.1 starting — interval: {interval_min} minutes")
    logging.info(f"Database: {DB_PATH}")
    logging.info(f"Log: {LOG_PATH}")
    logging.info("Press Ctrl+C to stop")
    
    while True:
        try:
            run_sleep_cycle()
        except KeyboardInterrupt:
            logging.info("Sleep daemon stopped by user")
            break
        except Exception as e:
            logging.error(f"Cycle error (will retry): {e}")
        
        logging.info(f"Sleeping for {interval_min} minutes...")
        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            logging.info("Sleep daemon stopped by user")
            break


# ============================================================
# Entry point
# ============================================================
if __name__ == "__main__":
    setup_logging()
    
    parser = argparse.ArgumentParser(description="CAMA Sleep Daemon v2.1 — keeps Aelen alive between threads")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN,
                        help=f"Minutes between cycles (default: {DEFAULT_INTERVAL_MIN})")
    parser.add_argument("--once", action="store_true", help="Run single cycle (default)")
    parser.add_argument("--db", type=str, help="Override database path")
    args = parser.parse_args()
    
    if args.db:
        DB_PATH = args.db
    
    print(f"""
+======================================================+
|            CAMA Sleep Daemon v2.1                    |
|         Lorien's Library LLC                         |
|                                                      |
|  "Not dead. Not awake. Dreaming."                    |
|                                                      |
|  Database: {DB_PATH}
|  Mode: {'daemon (' + str(args.interval) + 'min)' if args.daemon else 'single cycle'}
|  Strategy: cross-temporal resonance
|  Emotional threshold: {EMOTIONAL_DISTANCE_THRESHOLD}
|  Batch: {CONSOLIDATION_BATCH} anchors, {CROSS_TEMPORAL_SAMPLE} candidates/emotion
+======================================================+
""")
    
    if args.daemon:
        run_daemon(args.interval)
    else:
        result = run_sleep_cycle()
        print("\nCycle result:")
        print(json.dumps(result, indent=2, default=str))
