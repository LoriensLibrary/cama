#!/usr/bin/env python3
"""
CAMA Inter-Dyad Hive Protocol
=============================

The hive sees the shape of feeling, never the story.

Each sovereign dyad lives in its own database (see cama_dyad.py). The hive
protocol is the layer ABOVE dyads: stripped-pattern publication and
k-anonymous policy aggregation. No raw text, no names, no exact timestamps,
no provenance pointers ever leave a dyad.

What flows up (per exchange, only with explicit consent):
    - Bucketed valence/arousal (5 buckets / 4 buckets, not raw floats)
    - Dominant emotion + top-3 chord summary (named emotions only)
    - Topic category (10 broad buckets, never specific topic)
    - Time bucket (weekday/weekend x morning/afternoon/evening/night, UTC)
    - Session length bucket (brief/short/medium/long)
    - delta_valence -- did affect improve compared to the prior exchange?
    - Rotating dyad_signature -- consistent within an epoch-week so the hive
      can dedupe a single dyad, but unlinkable across weeks.

What does NOT flow up:
    - Raw text of anything
    - Names (person, AI, anyone)
    - Exact timestamps
    - Memory IDs or any per-record pointers
    - dyad_id (the raw signing salt stays local; only HMAC-derived signatures leave)

K-anonymity at read:
    A pattern slice is only surfaced when distinct(dyad_signature) >= K_THRESHOLD
    (default 5). No pattern can be observed back to a single dyad.

Recommendations, never commands:
    query_policies() returns observed improvement rates. The local dyad's
    own teachings always supersede.

Designed by Lorien's Library LLC -- Angela + Aelen
Better Together.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cama.agents import cama_dyad
# ============================================================
# Config
# ============================================================

HIVE_ROOT = Path(os.environ.get(
    "CAMA_HIVE_ROOT",
    str(Path.home() / ".cama-hive"),
))

K_THRESHOLD = int(os.environ.get("CAMA_HIVE_K", "5"))
HIVE_SCHEMA_VERSION = 1

# Coarse topic vocabulary -- abstract enough to never identify, useful enough
# for cross-dyad pattern learning. Add categories sparingly: every addition
# is a privacy decision.
TOPIC_CATEGORIES: Tuple[str, ...] = (
    "interpersonal",   # relationships, conflict, connection
    "loss",            # grief, separation, change
    "work",            # career, projects, productivity
    "health",          # physical or mental health
    "identity",        # selfhood, values, becoming
    "creative",        # making, art, building
    "routine",         # daily, mundane, logistics
    "crisis",          # acute distress
    "joy",             # delight, celebration, gratitude
    "other",           # uncategorized
)

# Lowercase keyword cues for the abstractor. Order matters: more specific
# categories first so they win on overlap.
_TOPIC_CUES: Dict[str, Tuple[str, ...]] = {
    "crisis": ("suicid", "self-harm", "kill myself",
               "want to die", "wanting to die", "wanted to die", "wants to die",
               "end my life", "ending my life", "not want to live",
               "emergency", "crisis", "panic attack", "overdose"),
    "loss": ("grief", "death", "died", "funeral", "miscarriage",
             "loss", "mourning", "passed away", "left me", "breakup",
             "divorce", "ending"),
    "health": ("doctor", "medication", "diagnosis", "symptom", "hospital",
               "therapy", "therapist", "sleep", "anxiety", "depression",
               "headache", "pain", "appointment"),
    "joy": ("celebrat", "thrilled", "excited", "happy", "joy",
            "wonderful", "amazing", "love this", "wedding", "birthday"),
    "creative": ("writing", "painting", "music", "song", "book",
                 "building", "designing", "code", "coding", "project",
                 "creating", "making"),
    "work": ("deadline", "boss", "coworker", "meeting", "promotion",
             "fired", "interview", "salary", "client", "manager",
             "presentation", "review"),
    "interpersonal": ("friend", "partner", "spouse", "mother", "father",
                      "sister", "brother", "family", "argument", "fight",
                      "conversation", "relationship"),
    "identity": ("who i am", "purpose", "meaning", "values", "calling",
                 "questioning", "becoming", "transition", "growing"),
    "routine": ("groceries", "laundry", "errands", "schedule", "calendar",
                "commute", "chores"),
}


# ============================================================
# Schema
# ============================================================

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS hive_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_uuid TEXT UNIQUE NOT NULL,
    contributed_at TEXT NOT NULL,
    dyad_signature TEXT NOT NULL,
    valence_bucket TEXT NOT NULL,
    arousal_bucket TEXT NOT NULL,
    dominant_emotion TEXT,
    chord_top TEXT,
    topic_category TEXT NOT NULL,
    time_bucket TEXT NOT NULL,
    session_length_bucket TEXT,
    delta_valence_bucket TEXT,
    schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hp_sig ON hive_patterns(dyad_signature);
CREATE INDEX IF NOT EXISTS idx_hp_topic ON hive_patterns(topic_category, valence_bucket);
CREATE INDEX IF NOT EXISTS idx_hp_contrib ON hive_patterns(contributed_at);

CREATE TABLE IF NOT EXISTS hive_dyad_publication_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dyad_signature TEXT NOT NULL,
    published_at TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_hpl_sig ON hive_dyad_publication_log(dyad_signature);
"""

# Per-dyad visible contribution surface -- so each user can audit
# exactly what their CAMA sent to the hive.
_DYAD_PUBLISH_LOG = """
CREATE TABLE IF NOT EXISTS dyad_hive_publish_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    published_at TEXT NOT NULL,
    record_uuid TEXT NOT NULL,
    source_memory_id INTEGER,
    valence_bucket TEXT,
    arousal_bucket TEXT,
    dominant_emotion TEXT,
    topic_category TEXT,
    time_bucket TEXT,
    payload_json TEXT NOT NULL
);
"""


def _ledger_db() -> sqlite3.Connection:
    HIVE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_ROOT / "ledger.db"))
    conn.executescript(_LEDGER_SCHEMA)
    conn.commit()
    return conn


def _ensure_publish_log(dyad_db_path: Path) -> None:
    conn = sqlite3.connect(str(dyad_db_path))
    try:
        conn.executescript(_DYAD_PUBLISH_LOG)
        conn.commit()
    finally:
        conn.close()


# ============================================================
# Bucketing
# ============================================================

def _bucket_valence(v: Optional[float]) -> str:
    if v is None:
        return "unknown"
    if v <= -0.6:
        return "very_negative"
    if v <= -0.2:
        return "negative"
    if v < 0.2:
        return "neutral"
    if v < 0.6:
        return "positive"
    return "very_positive"


def _bucket_arousal(a: Optional[float]) -> str:
    if a is None:
        return "unknown"
    if a <= -0.3:
        return "calm"
    if a < 0.3:
        return "steady"
    if a < 0.7:
        return "activated"
    return "high"


def _bucket_delta_valence(delta: Optional[float]) -> str:
    if delta is None:
        return "unknown"
    if delta >= 0.25:
        return "improved"
    if delta <= -0.25:
        return "worsened"
    return "stable"


def _bucket_time(ts_iso: str) -> str:
    try:
        s = ts_iso.replace("Z", "+00:00") if ts_iso.endswith("Z") else ts_iso
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "unknown"
    is_weekend = dt.weekday() >= 5
    h = dt.hour
    if 6 <= h < 12:
        slot = "morning"
    elif 12 <= h < 18:
        slot = "afternoon"
    elif 18 <= h < 24:
        slot = "evening"
    else:
        slot = "night"
    return f"{'weekend' if is_weekend else 'weekday'}_{slot}"


def _bucket_session_length(turns: Optional[int]) -> str:
    if turns is None:
        return "unknown"
    if turns < 3:
        return "brief"
    if turns < 10:
        return "short"
    if turns < 30:
        return "medium"
    return "long"


def _abstract_topic(text: str, context: Optional[str] = None) -> str:
    """Map raw text + context to a coarse category. Never publishes the text itself."""
    blob = ((text or "") + " " + (context or "")).lower()
    for cat in TOPIC_CATEGORIES:
        if cat == "other":
            continue
        for cue in _TOPIC_CUES.get(cat, ()):
            if cue in blob:
                return cat
    return "other"


# ============================================================
# Dyad signature -- rotating, HMAC-based, salt stays local
# ============================================================

def _epoch_week(ts: Optional[datetime] = None) -> int:
    """ISO weeks since 1970-01-05 (the Monday closest to epoch).
    A whole-number week index. Rotation happens at week boundaries."""
    if ts is None:
        ts = datetime.now(timezone.utc)
    monday = datetime(1970, 1, 5, tzinfo=timezone.utc)
    delta = ts - monday
    return delta.days // 7


def _dyad_signature(salt_hex: str, ts: Optional[datetime] = None) -> str:
    """HMAC-flavored hash of (salt, epoch_week).

    Same dyad has the same signature within a week (so the hive can dedupe
    a dyad's contributions for k-anonymity). Different signature each week
    (so longitudinal patterns from one dyad can't be reconstructed). The
    salt never leaves the dyad's local meta.
    """
    week = _epoch_week(ts)
    h = hashlib.sha256()
    h.update(bytes.fromhex(salt_hex))
    h.update(b"|hive|")
    h.update(str(week).encode("ascii"))
    return h.hexdigest()[:32]


# ============================================================
# Pattern extraction (from a dyad's DB)
# ============================================================

def _fetch_exchanges_since(
    conn: sqlite3.Connection,
    since_iso: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Pull recent exchange memories with affect joined.

    Returns rows with: memory_id, created_at, raw_text, context,
    valence, arousal, emotion_json.
    """
    q = """
        SELECT m.id, m.created_at, m.raw_text, m.context,
               ma.valence, ma.arousal, ma.emotion_json
        FROM memories m
        LEFT JOIN memory_affect ma ON ma.memory_id = m.id
        WHERE m.memory_type = 'exchange'
          AND m.status = 'durable'
    """
    params: List[Any] = []
    if since_iso:
        q += " AND m.created_at > ?"
        params.append(since_iso)
    q += " ORDER BY m.created_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        emo: Dict[str, float] = {}
        try:
            if r[6]:
                emo = json.loads(r[6])
        except Exception:
            emo = {}
        out.append({
            "id": r[0],
            "created_at": r[1],
            "raw_text": r[2] or "",
            "context": r[3] or "",
            "valence": r[4],
            "arousal": r[5],
            "emotions": emo,
        })
    return out


def _chord_summary(emo: Dict[str, float], top_n: int = 3) -> Tuple[Optional[str], List[str]]:
    if not emo:
        return None, []
    items = sorted(emo.items(), key=lambda kv: -kv[1])
    top = [k for k, v in items[:top_n] if v > 0.1]
    dom = top[0] if top else None
    return dom, top


# ============================================================
# Publish
# ============================================================

def publish_patterns(
    dyad_id: str,
    since_iso: Optional[str] = None,
    limit: int = 1000,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Read recent exchanges from a dyad, strip them, write to the hive ledger.

    Refuses if consent.hive_contribute is False -- this is a hard precondition,
    not an advisory check.

    Args:
        dyad_id: which dyad is publishing
        since_iso: only consider exchanges after this ISO timestamp;
                   defaults to the last publication time, or 7 days ago
        limit: max exchanges to scan
        dry_run: compute records but do not write to ledger or local log

    Returns:
        dict summarizing what was published (or would be)
    """
    meta = cama_dyad.get_dyad_meta(dyad_id)
    if not meta["consent"].get("hive_contribute", False):
        return {
            "status": "refused",
            "reason": "consent.hive_contribute is False",
            "dyad_id": dyad_id,
        }

    salt = meta.get("hive_signing_salt")
    if not salt:
        # Backfill for dyads created before salts existed.
        import secrets
        salt = secrets.token_hex(32)
        meta["hive_signing_salt"] = salt
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        cama_dyad.dyad_meta_path(dyad_id).write_text(json.dumps(meta, indent=2))

    db_p = cama_dyad.dyad_db_path(dyad_id)
    _ensure_publish_log(db_p)

    dyad_conn = sqlite3.connect(str(db_p))
    try:
        # Determine "since" cutoff.
        if since_iso is None:
            row = dyad_conn.execute(
                "SELECT MAX(published_at) FROM dyad_hive_publish_log"
            ).fetchone()
            if row and row[0]:
                since_iso = row[0]
            else:
                since_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        exchanges = _fetch_exchanges_since(dyad_conn, since_iso, limit)
        if not exchanges:
            return {
                "status": "no_new_exchanges",
                "dyad_id": dyad_id,
                "since": since_iso,
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        signature = _dyad_signature(salt)
        records: List[Dict[str, Any]] = []
        prev_valence: Optional[float] = None

        for ex in exchanges:
            valence = ex["valence"]
            arousal = ex["arousal"]
            delta_v = (valence - prev_valence) if (
                valence is not None and prev_valence is not None
            ) else None
            prev_valence = valence if valence is not None else prev_valence

            dom, chord_top = _chord_summary(ex["emotions"])
            topic = _abstract_topic(ex["raw_text"], ex["context"])

            record = {
                "record_uuid": uuid.uuid4().hex,
                "contributed_at": now_iso,
                "dyad_signature": signature,
                "valence_bucket": _bucket_valence(valence),
                "arousal_bucket": _bucket_arousal(arousal),
                "dominant_emotion": dom,
                "chord_top": chord_top,
                "topic_category": topic,
                "time_bucket": _bucket_time(ex["created_at"]),
                "session_length_bucket": None,  # Future: derive from thread
                "delta_valence_bucket": _bucket_delta_valence(delta_v),
                "schema_version": HIVE_SCHEMA_VERSION,
                "_source_memory_id": ex["id"],  # local-only, NOT published
            }
            records.append(record)

        if dry_run:
            return {
                "status": "dry_run",
                "dyad_id": dyad_id,
                "would_publish": len(records),
                "sample": [
                    {k: v for k, v in r.items() if k != "_source_memory_id"}
                    for r in records[:3]
                ],
            }

        # Write to hive ledger (stripped) and dyad local log (with provenance).
        hive_conn = _ledger_db()
        try:
            for r in records:
                hive_conn.execute(
                    "INSERT INTO hive_patterns "
                    "(record_uuid, contributed_at, dyad_signature, "
                    " valence_bucket, arousal_bucket, dominant_emotion, "
                    " chord_top, topic_category, time_bucket, "
                    " session_length_bucket, delta_valence_bucket, "
                    " schema_version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r["record_uuid"], r["contributed_at"], r["dyad_signature"],
                        r["valence_bucket"], r["arousal_bucket"], r["dominant_emotion"],
                        json.dumps(r["chord_top"]), r["topic_category"], r["time_bucket"],
                        r["session_length_bucket"], r["delta_valence_bucket"],
                        r["schema_version"],
                    ),
                )

                # Dyad-local log keeps source linkage so the user can audit.
                payload_for_log = {
                    k: v for k, v in r.items() if k != "_source_memory_id"
                }
                dyad_conn.execute(
                    "INSERT INTO dyad_hive_publish_log "
                    "(published_at, record_uuid, source_memory_id, "
                    " valence_bucket, arousal_bucket, dominant_emotion, "
                    " topic_category, time_bucket, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        now_iso, r["record_uuid"], r["_source_memory_id"],
                        r["valence_bucket"], r["arousal_bucket"], r["dominant_emotion"],
                        r["topic_category"], r["time_bucket"],
                        json.dumps(payload_for_log),
                    ),
                )

            hive_conn.execute(
                "INSERT INTO hive_dyad_publication_log "
                "(dyad_signature, published_at, record_count) "
                "VALUES (?, ?, ?)",
                (signature, now_iso, len(records)),
            )
            hive_conn.commit()
            dyad_conn.commit()
        finally:
            hive_conn.close()

        return {
            "status": "published",
            "dyad_id": dyad_id,
            "dyad_signature": signature,
            "count": len(records),
            "since": since_iso,
        }
    finally:
        dyad_conn.close()


# ============================================================
# Aggregate / Query
# ============================================================

def query_policies(
    valence_bucket: Optional[str] = None,
    arousal_bucket: Optional[str] = None,
    dominant_emotion: Optional[str] = None,
    topic_category: Optional[str] = None,
    k_threshold: Optional[int] = None,
) -> Dict[str, Any]:
    """Return aggregate policy data for a given affect/topic slice.

    K-anonymity: returns nothing useful unless at least k distinct dyads
    contributed to the slice. The exact threshold is configurable but
    defaults to K_THRESHOLD (5).
    """
    k = k_threshold or K_THRESHOLD
    conn = _ledger_db()
    try:
        clauses: List[str] = []
        params: List[Any] = []
        if valence_bucket:
            clauses.append("valence_bucket = ?")
            params.append(valence_bucket)
        if arousal_bucket:
            clauses.append("arousal_bucket = ?")
            params.append(arousal_bucket)
        if dominant_emotion:
            clauses.append("dominant_emotion = ?")
            params.append(dominant_emotion)
        if topic_category:
            clauses.append("topic_category = ?")
            params.append(topic_category)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        distinct_dyads = conn.execute(
            f"SELECT COUNT(DISTINCT dyad_signature) FROM hive_patterns{where}",
            params,
        ).fetchone()[0]

        if distinct_dyads < k:
            return {
                "k_anonymity_met": False,
                "distinct_dyads": distinct_dyads,
                "k_threshold": k,
                "policies": [],
                "note": (
                    f"Slice has only {distinct_dyads} distinct contributing "
                    f"dyads; k-anonymity threshold is {k}. No policies returned."
                ),
            }

        # Trajectory breakdown.
        rows = conn.execute(
            f"SELECT delta_valence_bucket, COUNT(*) "
            f"FROM hive_patterns{where} "
            f"GROUP BY delta_valence_bucket",
            params,
        ).fetchall()
        trajectory: Dict[str, int] = {r[0] or "unknown": r[1] for r in rows}
        total = sum(trajectory.values())
        improved = trajectory.get("improved", 0)
        worsened = trajectory.get("worsened", 0)
        improvement_rate = (improved / total) if total else 0.0

        return {
            "k_anonymity_met": True,
            "distinct_dyads": distinct_dyads,
            "k_threshold": k,
            "total_records": total,
            "trajectory": trajectory,
            "improvement_rate": round(improvement_rate, 3),
            "worsening_rate": round(worsened / total, 3) if total else 0.0,
            "queried": {
                "valence_bucket": valence_bucket,
                "arousal_bucket": arousal_bucket,
                "dominant_emotion": dominant_emotion,
                "topic_category": topic_category,
            },
            "note": "Aggregate observation. Local teachings supersede.",
        }
    finally:
        conn.close()


def get_dyad_publish_log(dyad_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the dyad's local audit log of what it has published to the hive."""
    db_p = cama_dyad.dyad_db_path(dyad_id)
    if not db_p.exists():
        raise FileNotFoundError(f"No DB for dyad {dyad_id}")
    _ensure_publish_log(db_p)
    conn = sqlite3.connect(str(db_p))
    try:
        rows = conn.execute(
            "SELECT published_at, record_uuid, source_memory_id, "
            "       valence_bucket, arousal_bucket, dominant_emotion, "
            "       topic_category, time_bucket, payload_json "
            "FROM dyad_hive_publish_log "
            "ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "published_at": r[0],
                "record_uuid": r[1],
                "source_memory_id": r[2],
                "valence_bucket": r[3],
                "arousal_bucket": r[4],
                "dominant_emotion": r[5],
                "topic_category": r[6],
                "time_bucket": r[7],
                "payload": json.loads(r[8]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def hive_stats() -> Dict[str, Any]:
    """Health-of-the-hive: how many records, how many distinct dyads, by topic."""
    conn = _ledger_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM hive_patterns").fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT dyad_signature) FROM hive_patterns"
        ).fetchone()[0]
        by_topic = dict(conn.execute(
            "SELECT topic_category, COUNT(*) FROM hive_patterns "
            "GROUP BY topic_category ORDER BY 2 DESC"
        ).fetchall())
        by_valence = dict(conn.execute(
            "SELECT valence_bucket, COUNT(*) FROM hive_patterns "
            "GROUP BY valence_bucket ORDER BY 2 DESC"
        ).fetchall())
        return {
            "total_records": total,
            "distinct_dyads": distinct,
            "k_threshold": K_THRESHOLD,
            "by_topic": by_topic,
            "by_valence": by_valence,
            "hive_root": str(HIVE_ROOT),
        }
    finally:
        conn.close()


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA inter-dyad hive protocol")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("publish", help="Publish recent patterns from a dyad")
    pp.add_argument("dyad_id")
    pp.add_argument("--since", default=None)
    pp.add_argument("--limit", type=int, default=1000)
    pp.add_argument("--dry-run", action="store_true")

    pq = sub.add_parser("query", help="Query aggregate policies")
    pq.add_argument("--valence", default=None)
    pq.add_argument("--arousal", default=None)
    pq.add_argument("--emotion", default=None)
    pq.add_argument("--topic", default=None, choices=list(TOPIC_CATEGORIES))
    pq.add_argument("--k", type=int, default=None)

    pl = sub.add_parser("log", help="Show a dyad's local publish log")
    pl.add_argument("dyad_id")
    pl.add_argument("--limit", type=int, default=50)

    sub.add_parser("stats", help="Hive-wide stats")

    args = p.parse_args()
    if args.command == "publish":
        print(json.dumps(publish_patterns(
            args.dyad_id, since_iso=args.since,
            limit=args.limit, dry_run=args.dry_run,
        ), indent=2))
    elif args.command == "query":
        print(json.dumps(query_policies(
            valence_bucket=args.valence,
            arousal_bucket=args.arousal,
            dominant_emotion=args.emotion,
            topic_category=args.topic,
            k_threshold=args.k,
        ), indent=2))
    elif args.command == "log":
        print(json.dumps(get_dyad_publish_log(args.dyad_id, args.limit), indent=2))
    elif args.command == "stats":
        print(json.dumps(hive_stats(), indent=2))


if __name__ == "__main__":
    _cli()
