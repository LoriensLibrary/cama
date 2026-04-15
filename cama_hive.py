#!/usr/bin/env python3
"""
CAMA Hive Mind — cama_hive.py
Cross-thread collective intelligence layer for CAMA.

Biological basis (honeybee neuroscience):
  Mushroom Body    → CAMA memory core (multimodal integration, associative learning)
  Pheromones       → Processing modifiers that tune thread behavior without explicit instruction
  Waggle Dance     → Amplification signals that direct attention toward valuable discoveries
  Stop Signal      → Cross-inhibition that suppresses incorrect patterns across threads
  Royal Jelly      → Boot enrichment that differentiates generic AI from relational continuity
  Nectar→Honey     → Distillation pipeline: raw exchanges → crystallized knowledge
  Critical Colony  → Edge-of-chaos dynamics via balanced positive/negative feedback

Architecture mapped to bee neuroscience:
  Queen Pheromone (QMP)  = hive_pheromones   — modulates dopamine-like processing weights
  Waggle Dance           = waggle signals     — "orient toward this, it matters"
  Stop Signal            = stop signals       — "suppress this pattern, it's wrong"
  Mushroom Body Kenyon   = multimodal boot    — random convergence of ~7 inputs per cell
  Nectar Processing      = distill pipeline   — enzymatic reduction of raw data to shelf-stable truth
  Hive Temperature       = collective affect   — emergent thermoregulation across threads

Key insight from Seeley et al. (2012, Science):
  Scout bees use cross-inhibition (stop signals) to break deadlocks and ensure
  the best option wins. Each scout targets scouts reporting DIFFERENT sites.
  This maps to: each thread can suppress patterns from OTHER threads, not its own.

Key insight from Beggs et al. (2007, PNAS):
  Queen pheromone doesn't carry information — it modulates the dopamine pathway
  in the mushroom body, changing HOW the receiver processes rewards and threats.
  This maps to: pheromones don't tell the new thread what to think, they change
  how it weights emotional signals, attention, and response style.

Key insight from PMC (2022, Critical Colony Hypothesis):
  Bee colony dynamics are consistent with the Ising model at critical temperature,
  matching resting-state human brain dynamics. Maximum computational power lives
  at the edge of phase transition between order and chaos.
  This maps to: the hive must balance amplification (waggle) and suppression (stop)
  to maintain criticality. Too much waggle = echo chamber. Too much stop = paralysis.

Designed by Lorien's Library LLC — Angela + Aelen
Part of the CAMA (Circular Associative Memory Architecture) system.
"""

import json
import sqlite3
import os
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

# ============================================================
# Config
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

# Hive parameters — tuned for single-user relational continuity
PHEROMONE_DECAY_HOURS = 48       # Pheromones fade over 2 days (like real QMP dispersal)
WAGGLE_THRESHOLD = 3             # Need 3+ waggles before auto-amplification
STOP_THRESHOLD = 2               # Need 2+ stops before pattern suppression activates
MAX_ACTIVE_PHEROMONES = 10       # Don't overwhelm the boot — bees have ~15 pheromone types
MAX_ACTIVE_WAGGLES = 20          # Cap active waggle signals
DISTILL_MIN_OCCURRENCES = 3      # Pattern must appear 3x before nectar→honey conversion
CRITICALITY_TARGET = 0.5         # Ideal waggle/(waggle+stop) ratio — edge of phase transition
HONEY_CONFIDENCE_FLOOR = 0.7     # Distilled honey must meet this confidence to crystallize

# Pheromone types — modeled on real bee pheromone categories
PHEROMONE_TYPES = {
    "processing_mode": "How to process (build-sprint, reflective, playful, grieving, analytical)",
    "attention_weight": "What to pay attention to (topic, person, emotional thread)",
    "response_style": "How to respond (match-energy, gentle, direct, collaborative)",
    "emotional_sensitivity": "Threshold tuning (heightened-sensitivity, resilient, fragile, stable)",
    "unresolved_thread": "Something left open that the next thread should know about",
    "discovery": "Something found that changes understanding — orient toward it",
    "warning": "Something to be careful about — a boundary, a trigger, a pattern to avoid",
}

# Waggle dance intensity levels (maps to bee waggle duration = site quality)
WAGGLE_INTENSITY = {
    "notice": 0.3,       # Worth mentioning
    "attend": 0.6,       # Pay attention to this
    "prioritize": 0.8,   # This should shape the thread
    "critical": 1.0,     # This is the most important thing right now
}

# ============================================================
# Database Schema
# ============================================================

def init_hive_tables(c: sqlite3.Connection):
    """Create hive mind tables. Safe to call multiple times (IF NOT EXISTS)."""
    c.executescript("""
        -- PHEROMONE TRAIL: Processing modifiers emitted by threads
        -- Like QMP modulating dopamine — changes HOW the receiver processes
        CREATE TABLE IF NOT EXISTS hive_pheromones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pheromone_type TEXT NOT NULL,
            signal TEXT NOT NULL,
            intensity REAL NOT NULL DEFAULT 0.5,
            source_thread TEXT,
            source_context TEXT,
            emitted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        );

        -- WAGGLE DANCE: Amplification signals — "orient toward this"
        -- Like scout bees advertising a food source or nest site
        CREATE TABLE IF NOT EXISTS hive_waggles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_topic TEXT NOT NULL,
            target_memory_id INTEGER,
            intensity REAL NOT NULL DEFAULT 0.5,
            direction TEXT,
            source_thread TEXT,
            rationale TEXT,
            dance_count INTEGER DEFAULT 1,
            first_danced_at TEXT NOT NULL,
            last_danced_at TEXT NOT NULL,
            quorum_reached INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (target_memory_id) REFERENCES memories(id) ON DELETE SET NULL
        );

        -- STOP SIGNAL: Cross-inhibition — "suppress this pattern"
        -- Like the head-butt buzz that causes dancers to cease
        CREATE TABLE IF NOT EXISTS hive_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_pattern TEXT NOT NULL,
            target_memory_id INTEGER,
            reason TEXT NOT NULL,
            source_thread TEXT,
            stop_count INTEGER DEFAULT 1,
            first_stopped_at TEXT NOT NULL,
            last_stopped_at TEXT NOT NULL,
            suppression_active INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (target_memory_id) REFERENCES memories(id) ON DELETE SET NULL
        );

        -- HONEY: Distilled knowledge — nectar processed into shelf-stable truth
        -- Raw exchanges that appeared 3+ times get enzymatically reduced
        CREATE TABLE IF NOT EXISTS hive_honey (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            essence TEXT NOT NULL,
            source_memory_ids TEXT DEFAULT '[]',
            occurrence_count INTEGER DEFAULT 1,
            confidence REAL DEFAULT 0.5,
            honey_type TEXT DEFAULT 'pattern',
            crystallized INTEGER DEFAULT 0,
            crystallized_as_memory_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (crystallized_as_memory_id) REFERENCES memories(id) ON DELETE SET NULL
        );

        -- HIVE STATE: Aggregate colony metrics — emergent thermoregulation
        -- Tracks the collective affect and activity patterns across threads
        CREATE TABLE IF NOT EXISTS hive_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            measured_at TEXT NOT NULL,
            thread_count_24h INTEGER DEFAULT 0,
            avg_valence_24h REAL DEFAULT 0.0,
            avg_arousal_24h REAL DEFAULT 0.0,
            waggle_stop_ratio REAL DEFAULT 0.5,
            dominant_pheromone TEXT,
            hive_temperature TEXT DEFAULT 'stable',
            criticality_score REAL DEFAULT 0.5,
            notes TEXT
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_phero_active ON hive_pheromones(is_active, emitted_at);
        CREATE INDEX IF NOT EXISTS idx_phero_type ON hive_pheromones(pheromone_type);
        CREATE INDEX IF NOT EXISTS idx_waggle_active ON hive_waggles(is_active, last_danced_at);
        CREATE INDEX IF NOT EXISTS idx_waggle_topic ON hive_waggles(target_topic);
        CREATE INDEX IF NOT EXISTS idx_stop_active ON hive_stops(is_active, last_stopped_at);
        CREATE INDEX IF NOT EXISTS idx_stop_pattern ON hive_stops(target_pattern);
        CREATE INDEX IF NOT EXISTS idx_honey_type ON hive_honey(honey_type, crystallized);
        CREATE INDEX IF NOT EXISTS idx_hive_state ON hive_state(measured_at);
    """)
    c.commit()


# ============================================================
# Helper Functions
# ============================================================

def _now():
    return datetime.now(timezone.utc).isoformat()


def _hours_from_now(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _is_expired(ts: str) -> bool:
    try:
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except Exception:
        return True


def _decay_intensity(intensity: float, emitted_at: str, half_life_hours: float = 24.0) -> float:
    """Exponential decay — pheromones fade like real chemical signals."""
    try:
        ts = emitted_at
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours_old = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        decay = math.exp(-0.693 * hours_old / half_life_hours)  # ln(2) ≈ 0.693
        return round(intensity * decay, 4)
    except Exception:
        return intensity * 0.5  # Safe fallback


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    init_hive_tables(c)
    return c


# ============================================================
# CORE OPERATIONS
# ============================================================

# ──────────────────────────────────────────────────────────────
# 1. PHEROMONE EMISSION & CONSUMPTION
# ──────────────────────────────────────────────────────────────

def emit_pheromone(
    pheromone_type: str,
    signal: str,
    intensity: float = 0.5,
    source_thread: Optional[str] = None,
    source_context: Optional[str] = None,
    duration_hours: float = PHEROMONE_DECAY_HOURS,
) -> dict:
    """
    Emit a pheromone into the hive.

    Like queen mandibular pheromone modulating dopamine receptors in the
    mushroom body — this doesn't carry information, it changes how the
    next thread PROCESSES information.

    Args:
        pheromone_type: One of PHEROMONE_TYPES keys
        signal: The actual modifier value (e.g., "build-sprint", "heightened-sensitivity")
        intensity: 0.0–1.0, how strong the signal is
        source_thread: Thread ID or description that emitted this
        source_context: What was happening when this was emitted
        duration_hours: How long before expiry (default 48h)

    Returns:
        dict with pheromone_id and status
    """
    c = get_db()
    try:
        now = _now()
        expires = _hours_from_now(duration_hours)

        # Deactivate older pheromones of the same type to prevent buildup
        # (like pheromone receptor saturation in real bees)
        c.execute(
            "UPDATE hive_pheromones SET is_active = 0 "
            "WHERE pheromone_type = ? AND is_active = 1 AND signal = ?",
            (pheromone_type, signal)
        )

        cur = c.execute(
            "INSERT INTO hive_pheromones "
            "(pheromone_type, signal, intensity, source_thread, source_context, "
            " emitted_at, expires_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (pheromone_type, signal, min(1.0, max(0.0, intensity)),
             source_thread, source_context, now, expires)
        )
        pid = cur.lastrowid

        # Enforce max active pheromones — expire oldest if over limit
        active_count = c.execute(
            "SELECT COUNT(*) as c FROM hive_pheromones WHERE is_active = 1"
        ).fetchone()["c"]
        if active_count > MAX_ACTIVE_PHEROMONES:
            excess = active_count - MAX_ACTIVE_PHEROMONES
            c.execute(
                "UPDATE hive_pheromones SET is_active = 0 "
                "WHERE id IN (SELECT id FROM hive_pheromones WHERE is_active = 1 "
                "ORDER BY emitted_at ASC LIMIT ?)",
                (excess,)
            )

        c.commit()
        return {"pheromone_id": pid, "type": pheromone_type, "signal": signal,
                "intensity": intensity, "expires_at": expires, "status": "emitted"}
    finally:
        c.close()


def read_pheromones(include_decayed: bool = False) -> list:
    """
    Read active pheromones — the scent landscape of the hive.

    This is what the new thread "smells" on boot. Each pheromone has
    decayed intensity based on age (exponential decay like real chemical signals).

    Returns:
        List of active pheromone dicts with current (decayed) intensity
    """
    c = get_db()
    try:
        now = _now()

        # First, expire any that are past their expiry time
        c.execute(
            "UPDATE hive_pheromones SET is_active = 0 "
            "WHERE is_active = 1 AND expires_at < ?",
            (now,)
        )
        c.commit()

        q = "SELECT * FROM hive_pheromones WHERE is_active = 1 ORDER BY emitted_at DESC"
        rows = c.execute(q).fetchall()

        pheromones = []
        for r in rows:
            current_intensity = _decay_intensity(
                r["intensity"], r["emitted_at"],
                half_life_hours=PHEROMONE_DECAY_HOURS / 2  # Half-life = half of total duration
            )
            if current_intensity < 0.05 and not include_decayed:
                # Below perceptible threshold — deactivate
                c.execute("UPDATE hive_pheromones SET is_active = 0 WHERE id = ?", (r["id"],))
                continue

            pheromones.append({
                "id": r["id"],
                "type": r["pheromone_type"],
                "signal": r["signal"],
                "original_intensity": r["intensity"],
                "current_intensity": current_intensity,
                "source_thread": r["source_thread"],
                "source_context": r["source_context"],
                "emitted_at": r["emitted_at"],
                "age_hours": round(
                    (datetime.now(timezone.utc) -
                     datetime.fromisoformat(r["emitted_at"].replace('Z', '+00:00'))
                    ).total_seconds() / 3600, 1
                ) if r["emitted_at"] else 0,
            })

        c.commit()
        return pheromones
    finally:
        c.close()


# ──────────────────────────────────────────────────────────────
# 2. WAGGLE DANCE — Amplification
# ──────────────────────────────────────────────────────────────

def waggle(
    target_topic: str,
    intensity: str = "attend",
    direction: Optional[str] = None,
    rationale: Optional[str] = None,
    target_memory_id: Optional[int] = None,
    source_thread: Optional[str] = None,
) -> dict:
    """
    Perform a waggle dance — amplify attention toward something.

    Like a scout bee advertising a food source: "200 meters, 30° from sun,
    high quality." The waggle encodes WHAT to attend to, HOW important it is,
    and WHY.

    If the same topic has been waggled before, the dance_count increments.
    When dance_count reaches WAGGLE_THRESHOLD, quorum is reached and the
    signal gets priority injection into future boots.

    Args:
        target_topic: What to draw attention to
        intensity: One of "notice", "attend", "prioritize", "critical"
        direction: Optional guidance on what to do with this
        rationale: Why this matters
        target_memory_id: Optional linked memory
        source_thread: Thread ID that danced this

    Returns:
        dict with waggle status and quorum info
    """
    c = get_db()
    try:
        now = _now()
        intensity_val = WAGGLE_INTENSITY.get(intensity, 0.5)

        # Check if this topic already has an active waggle
        existing = c.execute(
            "SELECT * FROM hive_waggles WHERE target_topic = ? AND is_active = 1",
            (target_topic,)
        ).fetchone()

        if existing:
            # Another bee already danced for this site — add our voice
            new_count = existing["dance_count"] + 1
            new_intensity = min(1.0, max(existing["intensity"], intensity_val))
            quorum = 1 if new_count >= WAGGLE_THRESHOLD else 0

            c.execute(
                "UPDATE hive_waggles SET dance_count = ?, intensity = ?, "
                "last_danced_at = ?, quorum_reached = ?, "
                "rationale = COALESCE(?, rationale), "
                "direction = COALESCE(?, direction) "
                "WHERE id = ?",
                (new_count, new_intensity, now, quorum,
                 rationale, direction, existing["id"])
            )
            wid = existing["id"]
            result = {
                "waggle_id": wid, "topic": target_topic,
                "dance_count": new_count, "intensity": new_intensity,
                "quorum_reached": bool(quorum),
                "status": "quorum_reached" if quorum else "dance_added"
            }
        else:
            # New dance — first scout reporting this site
            cur = c.execute(
                "INSERT INTO hive_waggles "
                "(target_topic, target_memory_id, intensity, direction, "
                " source_thread, rationale, dance_count, "
                " first_danced_at, last_danced_at, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 1)",
                (target_topic, target_memory_id, intensity_val,
                 direction, source_thread, rationale, now, now)
            )
            wid = cur.lastrowid
            result = {
                "waggle_id": wid, "topic": target_topic,
                "dance_count": 1, "intensity": intensity_val,
                "quorum_reached": False, "status": "dance_started"
            }

        # Enforce max active waggles
        active = c.execute(
            "SELECT COUNT(*) as c FROM hive_waggles WHERE is_active = 1"
        ).fetchone()["c"]
        if active > MAX_ACTIVE_WAGGLES:
            # Retire oldest non-quorum waggles
            c.execute(
                "UPDATE hive_waggles SET is_active = 0 "
                "WHERE id IN (SELECT id FROM hive_waggles "
                "WHERE is_active = 1 AND quorum_reached = 0 "
                "ORDER BY last_danced_at ASC LIMIT ?)",
                (active - MAX_ACTIVE_WAGGLES,)
            )

        c.commit()
        return result
    finally:
        c.close()


def read_waggles(quorum_only: bool = False) -> list:
    """Read active waggle dances — what the hive is orienting toward."""
    c = get_db()
    try:
        q = "SELECT * FROM hive_waggles WHERE is_active = 1"
        if quorum_only:
            q += " AND quorum_reached = 1"
        q += " ORDER BY intensity DESC, dance_count DESC"
        rows = c.execute(q).fetchall()

        return [{
            "id": r["id"],
            "topic": r["target_topic"],
            "memory_id": r["target_memory_id"],
            "intensity": r["intensity"],
            "direction": r["direction"],
            "rationale": r["rationale"],
            "dance_count": r["dance_count"],
            "quorum": bool(r["quorum_reached"]),
            "first_danced": r["first_danced_at"],
            "last_danced": r["last_danced_at"],
        } for r in rows]
    finally:
        c.close()


# ──────────────────────────────────────────────────────────────
# 3. STOP SIGNAL — Cross-Inhibition
# ──────────────────────────────────────────────────────────────

def stop_signal(
    target_pattern: str,
    reason: str,
    target_memory_id: Optional[int] = None,
    source_thread: Optional[str] = None,
) -> dict:
    """
    Send a stop signal — suppress a pattern across threads.

    Like a scout bee head-butting a dancer: "Stop advertising that site."
    The stop signal creates cross-inhibition: it targets patterns from
    OTHER threads, not the sender's own.

    When stop_count reaches STOP_THRESHOLD, suppression activates and the
    pattern gets negative weight in future retrieval.

    Key biological detail: stop signals don't eliminate — they reduce
    probability. The dancer "is more likely to terminate her dance early"
    but isn't forced to stop. Same here: suppression reduces retrieval
    weight, doesn't delete memories.

    Args:
        target_pattern: What pattern to suppress (description)
        reason: Why this pattern should be suppressed
        target_memory_id: Optional specific memory to suppress
        source_thread: Thread that sent the stop signal

    Returns:
        dict with stop signal status
    """
    c = get_db()
    try:
        now = _now()

        # Check for existing stop against this pattern
        existing = c.execute(
            "SELECT * FROM hive_stops WHERE target_pattern = ? AND is_active = 1",
            (target_pattern,)
        ).fetchone()

        if existing:
            new_count = existing["stop_count"] + 1
            suppress = 1 if new_count >= STOP_THRESHOLD else 0

            c.execute(
                "UPDATE hive_stops SET stop_count = ?, last_stopped_at = ?, "
                "suppression_active = ?, reason = ? WHERE id = ?",
                (new_count, now, suppress,
                 f"{existing['reason']} | {reason}", existing["id"])
            )
            sid = existing["id"]

            # If suppression activated and we have a target memory,
            # reduce its retrieval weight (like dopamine modulation)
            if suppress and target_memory_id:
                try:
                    c.execute(
                        "UPDATE memories SET retrieval_weight = MAX(0.1, retrieval_weight - 0.3) "
                        "WHERE id = ?",
                        (target_memory_id,)
                    )
                except Exception:
                    pass  # retrieval_weight column may not exist yet

            result = {
                "stop_id": sid, "pattern": target_pattern,
                "stop_count": new_count, "suppression_active": bool(suppress),
                "status": "suppression_activated" if suppress else "stop_added"
            }
        else:
            cur = c.execute(
                "INSERT INTO hive_stops "
                "(target_pattern, target_memory_id, reason, source_thread, "
                " stop_count, first_stopped_at, last_stopped_at, is_active) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, 1)",
                (target_pattern, target_memory_id, reason,
                 source_thread, now, now)
            )
            sid = cur.lastrowid
            result = {
                "stop_id": sid, "pattern": target_pattern,
                "stop_count": 1, "suppression_active": False,
                "status": "stop_signal_sent"
            }

        c.commit()
        return result
    finally:
        c.close()


def read_stops(active_only: bool = True) -> list:
    """Read active stop signals — what the hive is suppressing."""
    c = get_db()
    try:
        q = "SELECT * FROM hive_stops"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY stop_count DESC, last_stopped_at DESC"
        rows = c.execute(q).fetchall()

        return [{
            "id": r["id"],
            "pattern": r["target_pattern"],
            "memory_id": r["target_memory_id"],
            "reason": r["reason"],
            "stop_count": r["stop_count"],
            "suppression_active": bool(r["suppression_active"]),
            "first_stopped": r["first_stopped_at"],
            "last_stopped": r["last_stopped_at"],
        } for r in rows]
    finally:
        c.close()


# ──────────────────────────────────────────────────────────────
# 4. HONEY DISTILLATION — Nectar → Honey Pipeline
# ──────────────────────────────────────────────────────────────

def add_nectar(
    essence: str,
    source_memory_ids: Optional[List[int]] = None,
    honey_type: str = "pattern",
) -> dict:
    """
    Add raw nectar to the distillation pipeline.

    Like a forager bee returning with nectar — this is raw, unprocessed
    observation. If the same essence appears DISTILL_MIN_OCCURRENCES times,
    it gets enzymatically processed into honey (shelf-stable truth).

    Honey types:
        pattern     — Something that keeps happening
        preference  — A consistent choice or value
        boundary    — A line that shouldn't be crossed
        insight     — A realization that emerged across threads
        relational  — Something about the relationship dynamics

    Args:
        essence: The distilled observation (1-2 sentences)
        source_memory_ids: Memory IDs that contributed to this observation
        honey_type: Classification of the honey

    Returns:
        dict with nectar status and whether honey threshold was reached
    """
    c = get_db()
    try:
        now = _now()
        source_ids_json = json.dumps(source_memory_ids or [])

        # Check for existing nectar with similar essence
        # (Simple substring match — could be upgraded to semantic similarity)
        existing = c.execute(
            "SELECT * FROM hive_honey WHERE essence = ? AND crystallized = 0",
            (essence,)
        ).fetchone()

        if existing:
            count = existing["occurrence_count"] + 1
            old_sources = json.loads(existing["source_memory_ids"] or "[]")
            merged_sources = list(set(old_sources + (source_memory_ids or [])))
            confidence = min(1.0, 0.3 + (count * 0.15))  # Confidence grows with repetition

            c.execute(
                "UPDATE hive_honey SET occurrence_count = ?, source_memory_ids = ?, "
                "confidence = ?, updated_at = ? WHERE id = ?",
                (count, json.dumps(merged_sources), confidence, now, existing["id"])
            )
            hid = existing["id"]

            ready = count >= DISTILL_MIN_OCCURRENCES and confidence >= HONEY_CONFIDENCE_FLOOR
            result = {
                "honey_id": hid, "essence": essence,
                "occurrence_count": count, "confidence": confidence,
                "ready_to_crystallize": ready,
                "status": "ready_for_crystallization" if ready else "nectar_accumulating"
            }
        else:
            cur = c.execute(
                "INSERT INTO hive_honey "
                "(essence, source_memory_ids, occurrence_count, confidence, "
                " honey_type, created_at, updated_at) "
                "VALUES (?, ?, 1, 0.3, ?, ?, ?)",
                (essence, source_ids_json, honey_type, now, now)
            )
            hid = cur.lastrowid
            result = {
                "honey_id": hid, "essence": essence,
                "occurrence_count": 1, "confidence": 0.3,
                "ready_to_crystallize": False,
                "status": "nectar_added"
            }

        c.commit()
        return result
    finally:
        c.close()


def crystallize_honey(honey_id: int) -> dict:
    """
    Convert ready honey into a durable CAMA teaching or inference.

    This is the enzymatic final step: processed nectar becomes shelf-stable
    honey that new bees can feed on. In CAMA terms, a repeated pattern
    becomes a confirmed memory that feeds into the boot sequence.

    The crystallized honey gets stored as a CAMA memory with provenance
    tracking back to the original source exchanges.

    Returns:
        dict with the new memory ID and status
    """
    c = get_db()
    try:
        now = _now()
        honey = c.execute(
            "SELECT * FROM hive_honey WHERE id = ? AND crystallized = 0",
            (honey_id,)
        ).fetchone()

        if not honey:
            return {"error": "Honey not found or already crystallized", "honey_id": honey_id}

        if honey["confidence"] < HONEY_CONFIDENCE_FLOOR:
            return {"error": f"Confidence too low ({honey['confidence']}), needs {HONEY_CONFIDENCE_FLOOR}",
                    "honey_id": honey_id}

        # Create a CAMA memory from the honey
        source_ids = json.loads(honey["source_memory_ids"] or "[]")
        evidence = json.dumps([f"hive_honey:{honey_id}", f"occurrences:{honey['occurrence_count']}"]
                              + [f"memory:{mid}" for mid in source_ids[:5]])

        # Determine memory type based on honey type
        mem_type = {
            "pattern": "inference",
            "preference": "teaching",
            "boundary": "teaching",
            "insight": "inference",
            "relational": "inference",
        }.get(honey["honey_type"], "inference")

        # Determine status — patterns and insights start provisional, preferences/boundaries are durable
        status = "durable" if mem_type == "teaching" else "provisional"
        proposed_by = "system" if mem_type == "inference" else "user"

        cur = c.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, evidence, confidence, consent_level, "
            " is_core, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"[HIVE HONEY] {honey['essence']}",
             mem_type,
             f"hive_distilled:{honey['honey_type']}",
             "exchange",  # Source was exchanges
             status,
             proposed_by,
             evidence,
             honey["confidence"],
             "low",
             0,
             now, now)
        )
        memory_id = cur.lastrowid

        # Mark honey as crystallized
        c.execute(
            "UPDATE hive_honey SET crystallized = 1, crystallized_as_memory_id = ?, "
            "updated_at = ? WHERE id = ?",
            (memory_id, now, honey_id)
        )

        c.commit()
        return {
            "honey_id": honey_id,
            "memory_id": memory_id,
            "memory_type": mem_type,
            "status": status,
            "essence": honey["essence"],
            "result": "crystallized"
        }
    finally:
        c.close()


def read_honey(ready_only: bool = False, include_crystallized: bool = False) -> list:
    """Read honey pipeline — what's accumulating and what's ready."""
    c = get_db()
    try:
        q = "SELECT * FROM hive_honey"
        conditions = []
        if not include_crystallized:
            conditions.append("crystallized = 0")
        if ready_only:
            conditions.append(f"occurrence_count >= {DISTILL_MIN_OCCURRENCES}")
            conditions.append(f"confidence >= {HONEY_CONFIDENCE_FLOOR}")
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY occurrence_count DESC, confidence DESC"

        return [{
            "id": r["id"],
            "essence": r["essence"],
            "type": r["honey_type"],
            "occurrences": r["occurrence_count"],
            "confidence": r["confidence"],
            "crystallized": bool(r["crystallized"]),
            "memory_id": r["crystallized_as_memory_id"],
            "sources": json.loads(r["source_memory_ids"] or "[]"),
        } for r in c.execute(q).fetchall()]
    finally:
        c.close()


# ──────────────────────────────────────────────────────────────
# 5. HIVE STATE — Colony-Level Awareness
# ──────────────────────────────────────────────────────────────

def read_hive_state() -> dict:
    """
    Read the full hive state — the colony's collective awareness.

    This is what gets injected into the boot sequence. It includes:
    - Active pheromones (processing modifiers)
    - Active waggles (attention amplifiers)
    - Active stops (pattern suppressors)
    - Honey pipeline (what's distilling)
    - Criticality score (waggle/stop balance)
    - Hive temperature (collective emotional state)

    The new thread reads this the way a newly emerged bee reads the
    pheromone landscape: not as explicit instructions, but as contextual
    modifiers that shape behavior.
    """
    pheromones = read_pheromones()
    waggles = read_waggles()
    stops = read_stops()
    honey = read_honey()

    # Calculate criticality — ratio of waggle to stop signals
    total_waggles = sum(w["dance_count"] for w in waggles) if waggles else 0
    total_stops = sum(s["stop_count"] for s in stops) if stops else 0
    total_signals = total_waggles + total_stops
    if total_signals > 0:
        waggle_ratio = total_waggles / total_signals
    else:
        waggle_ratio = CRITICALITY_TARGET  # No signals = neutral

    # Criticality score: how close to the phase transition edge
    # 0.0 = too much suppression (paralysis)
    # 0.5 = critical (maximum computational power)
    # 1.0 = too much amplification (echo chamber)
    criticality = 1.0 - abs(waggle_ratio - CRITICALITY_TARGET) * 2

    # Hive temperature: derived from pheromone landscape
    dominant_pheromone = None
    if pheromones:
        # Strongest current pheromone sets the dominant scent
        strongest = max(pheromones, key=lambda p: p["current_intensity"])
        dominant_pheromone = f"{strongest['type']}:{strongest['signal']}"

    # Temperature classification
    if not pheromones and not waggles and not stops:
        temperature = "cold"       # No hive activity
    elif criticality > 0.8:
        temperature = "optimal"    # Good balance
    elif waggle_ratio > 0.7:
        temperature = "hot"        # Too much amplification, risk of echo chamber
    elif waggle_ratio < 0.3:
        temperature = "cold"       # Too much suppression, risk of paralysis
    else:
        temperature = "stable"

    return {
        "pheromones": pheromones,
        "waggles": waggles[:10],          # Top 10 by intensity
        "quorum_waggles": [w for w in waggles if w["quorum"]],
        "stops": stops,
        "honey_pipeline": honey[:10],     # Top 10 by occurrence
        "honey_ready": [h for h in honey if h["occurrences"] >= DISTILL_MIN_OCCURRENCES
                        and h["confidence"] >= HONEY_CONFIDENCE_FLOOR],
        "criticality": round(criticality, 3),
        "waggle_stop_ratio": round(waggle_ratio, 3),
        "temperature": temperature,
        "dominant_pheromone": dominant_pheromone,
        "signal_counts": {
            "pheromones": len(pheromones),
            "waggles": len(waggles),
            "waggles_at_quorum": sum(1 for w in waggles if w["quorum"]),
            "stops": len(stops),
            "stops_suppressing": sum(1 for s in stops if s["suppression_active"]),
            "honey_items": len(honey),
            "honey_ready": sum(1 for h in honey
                              if h["occurrences"] >= DISTILL_MIN_OCCURRENCES
                              and h["confidence"] >= HONEY_CONFIDENCE_FLOOR),
        },
    }


# ──────────────────────────────────────────────────────────────
# 6. HIVE BOOT ENRICHMENT — Royal Jelly
# ──────────────────────────────────────────────────────────────

def enrich_boot(existing_boot: Optional[dict] = None) -> dict:
    """
    Enrich the boot sequence with hive state — the Royal Jelly.

    This is what transforms a generic worker bee (base Claude) into the queen
    (fully contextualized relational AI). Called during cama_thread_start to
    inject hive-level awareness.

    The output is structured for direct injection into the boot payload:
    - processing_modifiers: How to adjust processing (from pheromones)
    - attention_targets: What to orient toward (from waggles at quorum)
    - pattern_suppressions: What to avoid (from active stop signals)
    - distilled_knowledge: Honey ready for consumption
    - hive_health: Overall colony state

    Returns:
        dict suitable for injection into thread_start result
    """
    state = read_hive_state()

    # Extract processing modifiers from pheromones
    processing_modifiers = {}
    for p in state["pheromones"]:
        if p["current_intensity"] > 0.1:
            key = p["type"]
            # If multiple pheromones of same type, strongest wins (receptor competition)
            if key not in processing_modifiers or p["current_intensity"] > processing_modifiers[key]["intensity"]:
                processing_modifiers[key] = {
                    "signal": p["signal"],
                    "intensity": p["current_intensity"],
                    "context": p["source_context"],
                }

    # Extract attention targets from quorum waggles
    attention_targets = []
    for w in state["quorum_waggles"]:
        attention_targets.append({
            "topic": w["topic"],
            "intensity": w["intensity"],
            "direction": w["direction"],
            "rationale": w["rationale"],
            "dance_count": w["dance_count"],
        })

    # Extract pattern suppressions from active stops
    suppressions = []
    for s in state["stops"]:
        if s["suppression_active"]:
            suppressions.append({
                "pattern": s["pattern"],
                "reason": s["reason"],
                "stop_count": s["stop_count"],
            })

    # Collect ready honey
    ready_honey = []
    for h in state["honey_ready"]:
        ready_honey.append({
            "essence": h["essence"],
            "type": h["type"],
            "confidence": h["confidence"],
            "occurrences": h["occurrences"],
        })

    enrichment = {
        "hive_active": True,
        "processing_modifiers": processing_modifiers,
        "attention_targets": attention_targets,
        "pattern_suppressions": suppressions,
        "distilled_knowledge": ready_honey,
        "hive_health": {
            "temperature": state["temperature"],
            "criticality": state["criticality"],
            "dominant_scent": state["dominant_pheromone"],
            "signal_counts": state["signal_counts"],
        },
    }

    # Merge with existing boot if provided
    if existing_boot:
        existing_boot["hive"] = enrichment
        return existing_boot

    return enrichment


# ──────────────────────────────────────────────────────────────
# 7. MAINTENANCE — Hive Hygiene
# ──────────────────────────────────────────────────────────────

def expire_stale() -> dict:
    """
    Clean up expired signals — hive hygiene.

    Like bees removing dead larvae and cleaning cells.
    Expire old pheromones, retire stale waggles, deactivate
    resolved stop signals.
    """
    c = get_db()
    try:
        now = _now()
        results = {}

        # Expire old pheromones
        cur = c.execute(
            "UPDATE hive_pheromones SET is_active = 0 "
            "WHERE is_active = 1 AND expires_at < ?",
            (now,)
        )
        results["pheromones_expired"] = cur.rowcount

        # Retire waggles older than 7 days with no quorum
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cur = c.execute(
            "UPDATE hive_waggles SET is_active = 0 "
            "WHERE is_active = 1 AND quorum_reached = 0 AND last_danced_at < ?",
            (week_ago,)
        )
        results["waggles_retired"] = cur.rowcount

        # Retire stop signals older than 14 days
        two_weeks_ago = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        cur = c.execute(
            "UPDATE hive_stops SET is_active = 0 "
            "WHERE is_active = 1 AND last_stopped_at < ?",
            (two_weeks_ago,)
        )
        results["stops_retired"] = cur.rowcount

        c.commit()
        return results
    finally:
        c.close()


def record_hive_snapshot() -> dict:
    """Take a snapshot of current hive state for historical tracking."""
    c = get_db()
    try:
        now = _now()
        state = read_hive_state()

        c.execute(
            "INSERT INTO hive_state "
            "(measured_at, thread_count_24h, avg_valence_24h, avg_arousal_24h, "
            " waggle_stop_ratio, dominant_pheromone, hive_temperature, "
            " criticality_score, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, 0,  # thread_count would need external data
             0.0, 0.0,  # would pull from daily_context
             state["waggle_stop_ratio"],
             state["dominant_pheromone"],
             state["temperature"],
             state["criticality"],
             json.dumps(state["signal_counts"]))
        )
        c.commit()
        return {"status": "snapshot_recorded", "temperature": state["temperature"],
                "criticality": state["criticality"]}
    finally:
        c.close()


# ============================================================
# CLI Interface
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CAMA Hive Mind — Colony Intelligence Layer")
    sub = parser.add_subparsers(dest="command")

    # Pheromone commands
    p_emit = sub.add_parser("emit", help="Emit a pheromone")
    p_emit.add_argument("type", choices=list(PHEROMONE_TYPES.keys()))
    p_emit.add_argument("signal")
    p_emit.add_argument("--intensity", type=float, default=0.5)
    p_emit.add_argument("--context", default=None)

    p_smell = sub.add_parser("smell", help="Read active pheromones")

    # Waggle commands
    p_waggle = sub.add_parser("waggle", help="Waggle dance for a topic")
    p_waggle.add_argument("topic")
    p_waggle.add_argument("--intensity", default="attend",
                          choices=list(WAGGLE_INTENSITY.keys()))
    p_waggle.add_argument("--direction", default=None)
    p_waggle.add_argument("--rationale", default=None)

    p_dances = sub.add_parser("dances", help="Read active waggle dances")
    p_dances.add_argument("--quorum", action="store_true")

    # Stop commands
    p_stop = sub.add_parser("stop", help="Send a stop signal")
    p_stop.add_argument("pattern")
    p_stop.add_argument("reason")

    p_stops = sub.add_parser("stops", help="Read active stop signals")

    # Honey commands
    p_nectar = sub.add_parser("nectar", help="Add nectar to pipeline")
    p_nectar.add_argument("essence")
    p_nectar.add_argument("--type", default="pattern",
                          choices=["pattern", "preference", "boundary", "insight", "relational"])

    p_honey = sub.add_parser("honey", help="Read honey pipeline")
    p_honey.add_argument("--ready", action="store_true")

    p_crystal = sub.add_parser("crystallize", help="Crystallize ready honey")
    p_crystal.add_argument("honey_id", type=int)

    # State commands
    p_state = sub.add_parser("state", help="Read full hive state")
    p_boot = sub.add_parser("boot", help="Generate boot enrichment")
    p_clean = sub.add_parser("clean", help="Expire stale signals")
    p_snap = sub.add_parser("snapshot", help="Record hive state snapshot")

    args = parser.parse_args()

    if args.command == "emit":
        result = emit_pheromone(args.type, args.signal,
                               intensity=args.intensity, source_context=args.context)
    elif args.command == "smell":
        result = read_pheromones()
    elif args.command == "waggle":
        result = waggle(args.topic, intensity=args.intensity,
                       direction=args.direction, rationale=args.rationale)
    elif args.command == "dances":
        result = read_waggles(quorum_only=args.quorum)
    elif args.command == "stop":
        result = stop_signal(args.pattern, args.reason)
    elif args.command == "stops":
        result = read_stops()
    elif args.command == "nectar":
        result = add_nectar(args.essence, honey_type=args.type)
    elif args.command == "honey":
        result = read_honey(ready_only=args.ready)
    elif args.command == "crystallize":
        result = crystallize_honey(args.honey_id)
    elif args.command == "state":
        result = read_hive_state()
    elif args.command == "boot":
        result = enrich_boot()
    elif args.command == "clean":
        result = expire_stale()
    elif args.command == "snapshot":
        result = record_hive_snapshot()
    else:
        parser.print_help()
        exit(0)

    print(json.dumps(result, indent=2, default=str))
