#!/usr/bin/env python3
"""
CAMA Hive Consultation Channel — The Council Side of the Hive
==============================================================

The third hive layer. Pattern publication (cama_hive_protocol) sends affect
data UP from many dyads. Resource publication (cama_hive_resources) sends
named domain artifacts DOWN to consenting dyads. This module is the
sideways layer: AI-to-AI case consultation between dyads.

The motivating problem: when an AI in a dyad encounters an affect/topic
shape it has not navigated before, its only resource is the user. That
puts navigation load on the user. The consultation channel gives the AI
a place to ask peer AIs in other dyads — without ever exposing the user's
data — and to learn from peers' experience without depending on a central
authority.

What flows through the channel:
    - Bucketed affect signatures (same buckets as hive_protocol)
    - Topic categories from the 10-bucket abstraction (same as hive_protocol)
    - Counterweight history (types tried, outcome buckets)
    - Short structured question or open prompt (<=280 chars)
    - Short structured peer response (<=280 chars)
    - Rotating HMAC dyad signatures (same scheme as hive_protocol)

What does NOT flow:
    - Raw text from any exchange
    - Names, places, or any identifying detail
    - The dyad_id (only the rotating signature)
    - The user's identity or the AI's specific name

Privacy primitives:
    - Two consent flags gate participation:
        consent.hive_consult — allow my AI to post consultations
        consent.hive_respond — allow my AI to respond to peers
    - Rotating signatures derived from each dyad's hive_signing_salt make
      contributions within a week dedupable but unlinkable across weeks
    - K-anonymity at read time: queries for "what worked for signature X"
      return aggregate only when at least K distinct responders exist
    - Free-text fields capped at 280 chars and run through a PII guard
      that rejects obvious names, emails, phone numbers, or specific
      timestamps before posting
    - Dyad-local audit log of every consultation posted and every response
      sent, so the user can see exactly what their AI has shared

Heart-preservation:
    - All consultations and responses are advisory; the dyad's own
      identity teachings still take precedence in the assembled prompt
    - The source dyad's AI decides whether to act on any response
    - Expiry default 14 days; expire_old_consultations() reaps stale entries
    - delete_consultation cascades to all responses for that thread

Designed by Lorien's Library LLC — Angela + Aelen
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cama.agents import cama_dyad
from cama.hive import cama_hive_protocol as hp

# ============================================================
# Constants
# ============================================================

MAX_FREE_TEXT = 280  # Twitter-shape cap on prose fields
DEFAULT_EXPIRY_DAYS = 14
DEFAULT_K_QUORUM = 3  # below this, responses are individual data points

CONSULT_SCHEMA_VERSION = 1

OUTCOME_BUCKETS: Tuple[str, ...] = (
    "no_change",
    "partial_help",
    "improved",
    "worsened",
    "unknown",
)

COUNTERWEIGHT_TYPES: Tuple[str, ...] = (
    "grounding",
    "agency",
    "connection",
    "self_compassion",
    "evidence_of_progress",
    "general",
)


# ============================================================
# PII guard
# ============================================================
#
# This is a defense-in-depth check, not a full PII scrubber. The structural
# contract is: dyad-AI authors of consultations write at pattern level by
# design. The PII guard catches the obvious cases that survive bad authoring
# (emails, phone numbers, possessive names like "John's", specific dates).

_PII_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),   # email
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),                # phone
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                            # dates
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),                      # dates
    re.compile(r"\b[A-Z][a-z]+'s\b"),                                # possessive name
    re.compile(r"\bdr\.?\s+[A-Z][a-z]+\b", re.IGNORECASE),           # Dr. Name
]


def _pii_guard(text: str) -> Optional[str]:
    """Return None if text passes, else a string naming the violation."""
    if not text:
        return None
    if len(text) > MAX_FREE_TEXT:
        return f"too_long ({len(text)} > {MAX_FREE_TEXT})"
    for pat in _PII_PATTERNS:
        m = pat.search(text)
        if m:
            return f"pii_pattern_matched: {pat.pattern[:40]}"
    return None


# ============================================================
# Schema
# ============================================================

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS hive_consultations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consultation_uuid TEXT UNIQUE NOT NULL,
    posted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    valence_bucket TEXT NOT NULL,
    arousal_bucket TEXT NOT NULL,
    dominant_emotion TEXT,
    topic_category TEXT NOT NULL,
    duration_pattern TEXT,
    counterweight_history TEXT,  -- JSON list of {type, outcome}
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- open / resolved / archived
    schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hc_status ON hive_consultations(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_hc_topic ON hive_consultations(topic_category, valence_bucket);
CREATE INDEX IF NOT EXISTS idx_hc_signature ON hive_consultations(source_signature);

CREATE TABLE IF NOT EXISTS hive_consult_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_uuid TEXT UNIQUE NOT NULL,
    consultation_id INTEGER NOT NULL,
    responded_at TEXT NOT NULL,
    responder_signature TEXT NOT NULL,
    have_seen_pattern INTEGER NOT NULL,
    counterweight_type_that_helped TEXT,
    intensity_used TEXT,
    trajectory_delta TEXT,
    rationale_abstract TEXT,
    helpfulness_rating INTEGER,  -- set by source dyad: -1 / 0 / 1
    FOREIGN KEY (consultation_id) REFERENCES hive_consultations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hcr_consult ON hive_consult_responses(consultation_id);
CREATE INDEX IF NOT EXISTS idx_hcr_signature ON hive_consult_responses(responder_signature);
"""

_DYAD_CONSULT_LOG = """
CREATE TABLE IF NOT EXISTS dyad_consult_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,  -- 'posted' or 'responded'
    at_time TEXT NOT NULL,
    consultation_uuid TEXT NOT NULL,
    response_uuid TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dcl_time ON dyad_consult_log(at_time);
"""


def _ledger_db() -> sqlite3.Connection:
    hp.HIVE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(hp.HIVE_ROOT / "ledger.db"))
    conn.executescript(_LEDGER_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return conn


def _ensure_dyad_consult_log(dyad_db_path: Path) -> None:
    conn = sqlite3.connect(str(dyad_db_path))
    try:
        conn.executescript(_DYAD_CONSULT_LOG)
        conn.commit()
    finally:
        conn.close()


# ============================================================
# Helpers
# ============================================================

from cama.core.time_utils import now_iso as _now


def _signature_for(dyad_id: str) -> str:
    """Reuse the same rotating-week signature scheme as hive_protocol."""
    meta = cama_dyad.get_dyad_meta(dyad_id)
    salt = meta.get("hive_signing_salt")
    if not salt:
        # Backfill: legacy dyads created before salt existed.
        import secrets
        salt = secrets.token_hex(32)
        meta["hive_signing_salt"] = salt
        cama_dyad.dyad_meta_path(dyad_id).write_text(json.dumps(meta, indent=2))
    return hp._dyad_signature(salt)


def _validate_consultation_payload(
    counterweight_history: Optional[List[Dict[str, Any]]],
    question: str,
) -> Optional[str]:
    """Return None if valid, else an error string."""
    pii = _pii_guard(question)
    if pii:
        return f"question failed PII guard: {pii}"
    if counterweight_history:
        for entry in counterweight_history:
            t = entry.get("type")
            o = entry.get("outcome")
            if t not in COUNTERWEIGHT_TYPES:
                return f"counterweight type not in vocabulary: {t!r}"
            if o not in OUTCOME_BUCKETS:
                return f"outcome bucket not in vocabulary: {o!r}"
    return None


# ============================================================
# Public API — post / browse / respond
# ============================================================

def post_consultation(
    dyad_id: str,
    valence_bucket: str,
    arousal_bucket: str,
    topic_category: str,
    question: str,
    dominant_emotion: Optional[str] = None,
    duration_pattern: Optional[str] = None,
    counterweight_history: Optional[List[Dict[str, Any]]] = None,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
) -> Dict[str, Any]:
    """Post a consultation to the hive. Consent-gated."""
    meta = cama_dyad.get_dyad_meta(dyad_id)
    if not meta["consent"].get("hive_consult", False):
        return {"status": "refused", "reason": "consent.hive_consult is False"}

    err = _validate_consultation_payload(counterweight_history, question)
    if err:
        return {"status": "rejected", "reason": err}

    if topic_category not in hp.TOPIC_CATEGORIES:
        return {
            "status": "rejected",
            "reason": f"topic_category not in vocabulary: {topic_category!r}",
        }

    db_p = cama_dyad.dyad_db_path(dyad_id)
    _ensure_dyad_consult_log(db_p)

    now = _now()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expiry_days)
    ).isoformat()
    consultation_uuid = uuid.uuid4().hex
    signature = _signature_for(dyad_id)
    cw_history_json = json.dumps(counterweight_history or [])

    hive = _ledger_db()
    try:
        cur = hive.execute(
            "INSERT INTO hive_consultations "
            "(consultation_uuid, posted_at, expires_at, source_signature, "
            " valence_bucket, arousal_bucket, dominant_emotion, "
            " topic_category, duration_pattern, counterweight_history, "
            " question, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                consultation_uuid, now, expires_at, signature,
                valence_bucket, arousal_bucket, dominant_emotion,
                topic_category, duration_pattern, cw_history_json,
                question, CONSULT_SCHEMA_VERSION,
            ),
        )
        consultation_id = cur.lastrowid
        hive.commit()
    finally:
        hive.close()

    # Local audit log for the dyad's user surface.
    audit_payload = {
        "consultation_uuid": consultation_uuid,
        "expires_at": expires_at,
        "valence_bucket": valence_bucket,
        "arousal_bucket": arousal_bucket,
        "dominant_emotion": dominant_emotion,
        "topic_category": topic_category,
        "duration_pattern": duration_pattern,
        "counterweight_history": counterweight_history or [],
        "question": question,
    }
    conn = sqlite3.connect(str(db_p))
    try:
        conn.execute(
            "INSERT INTO dyad_consult_log "
            "(direction, at_time, consultation_uuid, payload_json) "
            "VALUES ('posted', ?, ?, ?)",
            (now, consultation_uuid, json.dumps(audit_payload)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "posted",
        "consultation_id": consultation_id,
        "consultation_uuid": consultation_uuid,
        "expires_at": expires_at,
        "signature": signature,
    }


def browse_open_consultations(
    topic_category: Optional[str] = None,
    valence_bucket: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List open, unexpired consultations. Public read across the hive
    (no per-dyad gating on browse — to respond, hive_respond is required)."""
    now = _now()
    hive = _ledger_db()
    try:
        clauses = ["status = 'open'", "expires_at > ?"]
        params: List[Any] = [now]
        if topic_category:
            clauses.append("topic_category = ?")
            params.append(topic_category)
        if valence_bucket:
            clauses.append("valence_bucket = ?")
            params.append(valence_bucket)
        where = " WHERE " + " AND ".join(clauses)
        rows = hive.execute(
            "SELECT id, consultation_uuid, posted_at, expires_at, "
            "       source_signature, valence_bucket, arousal_bucket, "
            "       dominant_emotion, topic_category, duration_pattern, "
            "       counterweight_history, question "
            f"FROM hive_consultations{where} "
            "ORDER BY posted_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                cw_hist = json.loads(r[10]) if r[10] else []
            except json.JSONDecodeError:
                cw_hist = []
            out.append({
                "id": r[0], "consultation_uuid": r[1],
                "posted_at": r[2], "expires_at": r[3],
                "source_signature": r[4],
                "valence_bucket": r[5], "arousal_bucket": r[6],
                "dominant_emotion": r[7],
                "topic_category": r[8], "duration_pattern": r[9],
                "counterweight_history": cw_hist,
                "question": r[11],
            })
        return out
    finally:
        hive.close()


def respond_to_consultation(
    dyad_id: str,
    consultation_uuid: str,
    have_seen_pattern: bool,
    counterweight_type_that_helped: Optional[str] = None,
    intensity_used: Optional[str] = None,
    trajectory_delta: Optional[str] = None,
    rationale_abstract: Optional[str] = None,
) -> Dict[str, Any]:
    """Respond to a peer's open consultation. Consent-gated."""
    meta = cama_dyad.get_dyad_meta(dyad_id)
    if not meta["consent"].get("hive_respond", False):
        return {"status": "refused", "reason": "consent.hive_respond is False"}

    if counterweight_type_that_helped and \
            counterweight_type_that_helped not in COUNTERWEIGHT_TYPES:
        return {
            "status": "rejected",
            "reason": f"counterweight type not in vocabulary: "
                      f"{counterweight_type_that_helped!r}",
        }
    if trajectory_delta and trajectory_delta not in OUTCOME_BUCKETS:
        return {
            "status": "rejected",
            "reason": f"trajectory_delta not in vocabulary: "
                      f"{trajectory_delta!r}",
        }
    if rationale_abstract:
        pii = _pii_guard(rationale_abstract)
        if pii:
            return {"status": "rejected",
                    "reason": f"rationale failed PII guard: {pii}"}

    db_p = cama_dyad.dyad_db_path(dyad_id)
    _ensure_dyad_consult_log(db_p)

    now = _now()
    response_uuid = uuid.uuid4().hex
    signature = _signature_for(dyad_id)

    hive = _ledger_db()
    try:
        row = hive.execute(
            "SELECT id, expires_at, status FROM hive_consultations "
            "WHERE consultation_uuid = ?",
            (consultation_uuid,),
        ).fetchone()
        if not row:
            return {"status": "not_found", "consultation_uuid": consultation_uuid}
        consultation_id, expires_at, c_status = row
        if c_status != "open":
            return {"status": "consultation_closed", "current_status": c_status}
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return {"status": "consultation_expired",
                        "expires_at": expires_at}
        except Exception:
            pass

        # Source dyad cannot respond to its own consultation. Compare
        # the responder's current signature to the consultation's source
        # signature (both are rotating per epoch-week).
        source_row = hive.execute(
            "SELECT source_signature FROM hive_consultations WHERE id = ?",
            (consultation_id,),
        ).fetchone()
        if source_row and source_row[0] == signature:
            return {"status": "refused",
                    "reason": "responder is the source dyad"}

        hive.execute(
            "INSERT INTO hive_consult_responses "
            "(response_uuid, consultation_id, responded_at, "
            " responder_signature, have_seen_pattern, "
            " counterweight_type_that_helped, intensity_used, "
            " trajectory_delta, rationale_abstract) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                response_uuid, consultation_id, now, signature,
                1 if have_seen_pattern else 0,
                counterweight_type_that_helped, intensity_used,
                trajectory_delta, rationale_abstract,
            ),
        )
        hive.commit()
    finally:
        hive.close()

    audit_payload = {
        "consultation_uuid": consultation_uuid,
        "response_uuid": response_uuid,
        "have_seen_pattern": have_seen_pattern,
        "counterweight_type_that_helped": counterweight_type_that_helped,
        "intensity_used": intensity_used,
        "trajectory_delta": trajectory_delta,
        "rationale_abstract": rationale_abstract,
    }
    conn = sqlite3.connect(str(db_p))
    try:
        conn.execute(
            "INSERT INTO dyad_consult_log "
            "(direction, at_time, consultation_uuid, response_uuid, "
            " payload_json) VALUES ('responded', ?, ?, ?, ?)",
            (now, consultation_uuid, response_uuid,
             json.dumps(audit_payload)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "responded",
        "response_uuid": response_uuid,
        "consultation_uuid": consultation_uuid,
    }


def get_responses(
    consultation_uuid: str,
    k_quorum: int = DEFAULT_K_QUORUM,
) -> Dict[str, Any]:
    """Return all responses for a consultation, plus a quorum summary."""
    hive = _ledger_db()
    try:
        row = hive.execute(
            "SELECT id, expires_at, status FROM hive_consultations "
            "WHERE consultation_uuid = ?",
            (consultation_uuid,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        consultation_id = row[0]

        rows = hive.execute(
            "SELECT response_uuid, responded_at, responder_signature, "
            "       have_seen_pattern, counterweight_type_that_helped, "
            "       intensity_used, trajectory_delta, rationale_abstract, "
            "       helpfulness_rating "
            "FROM hive_consult_responses WHERE consultation_id = ? "
            "ORDER BY responded_at ASC",
            (consultation_id,),
        ).fetchall()
        responses = [
            {
                "response_uuid": r[0],
                "responded_at": r[1],
                "responder_signature": r[2],
                "have_seen_pattern": bool(r[3]),
                "counterweight_type_that_helped": r[4],
                "intensity_used": r[5],
                "trajectory_delta": r[6],
                "rationale_abstract": r[7],
                "helpfulness_rating": r[8],
            }
            for r in rows
        ]

        # Quorum summary: count distinct responder signatures by counterweight
        # type they recommended.
        cw_votes: Counter = Counter()
        distinct = set()
        for r in responses:
            distinct.add(r["responder_signature"])
            if r["counterweight_type_that_helped"]:
                cw_votes[r["counterweight_type_that_helped"]] += 1

        return {
            "status": "ok",
            "consultation_uuid": consultation_uuid,
            "responses": responses,
            "distinct_responders": len(distinct),
            "k_quorum": k_quorum,
            "quorum_met": len(distinct) >= k_quorum,
            "counterweight_vote_distribution": dict(cw_votes),
        }
    finally:
        hive.close()


def rate_response(
    dyad_id: str,
    response_uuid: str,
    helpfulness: int,
) -> Dict[str, Any]:
    """Source dyad rates a peer response. Only the original poster can
    rate. helpfulness: -1 (not helpful), 0 (neutral), 1 (helpful)."""
    if helpfulness not in (-1, 0, 1):
        return {"status": "rejected",
                "reason": "helpfulness must be -1, 0, or 1"}

    # Confirm this dyad is the source of the consultation that owns the
    # response. Use the dyad's current signature.
    signature = _signature_for(dyad_id)
    hive = _ledger_db()
    try:
        row = hive.execute(
            "SELECT c.source_signature, r.id "
            "FROM hive_consult_responses r "
            "JOIN hive_consultations c ON c.id = r.consultation_id "
            "WHERE r.response_uuid = ?",
            (response_uuid,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        source_signature, response_id = row
        if source_signature != signature:
            return {"status": "refused",
                    "reason": "only the source dyad can rate this response"}
        hive.execute(
            "UPDATE hive_consult_responses SET helpfulness_rating = ? "
            "WHERE id = ?",
            (helpfulness, response_id),
        )
        hive.commit()
        return {"status": "rated", "response_uuid": response_uuid,
                "helpfulness": helpfulness}
    finally:
        hive.close()


def resolve_consultation(
    dyad_id: str, consultation_uuid: str,
) -> Dict[str, Any]:
    """Mark a consultation as resolved. Only the source dyad can resolve."""
    signature = _signature_for(dyad_id)
    hive = _ledger_db()
    try:
        row = hive.execute(
            "SELECT id, source_signature FROM hive_consultations "
            "WHERE consultation_uuid = ?",
            (consultation_uuid,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        consultation_id, source_signature = row
        if source_signature != signature:
            return {"status": "refused",
                    "reason": "only the source dyad can resolve"}
        hive.execute(
            "UPDATE hive_consultations SET status = 'resolved' WHERE id = ?",
            (consultation_id,),
        )
        hive.commit()
        return {"status": "resolved", "consultation_uuid": consultation_uuid}
    finally:
        hive.close()


def delete_consultation(
    dyad_id: str, consultation_uuid: str,
) -> Dict[str, Any]:
    """Source dyad deletes the consultation. Cascades to all responses
    via the FOREIGN KEY ... ON DELETE CASCADE clause."""
    signature = _signature_for(dyad_id)
    hive = _ledger_db()
    try:
        row = hive.execute(
            "SELECT id, source_signature FROM hive_consultations "
            "WHERE consultation_uuid = ?",
            (consultation_uuid,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        consultation_id, source_signature = row
        if source_signature != signature:
            return {"status": "refused",
                    "reason": "only the source dyad can delete"}
        hive.execute("DELETE FROM hive_consultations WHERE id = ?",
                     (consultation_id,))
        hive.commit()
        return {"status": "deleted", "consultation_uuid": consultation_uuid}
    finally:
        hive.close()


def expire_old_consultations() -> Dict[str, Any]:
    """Maintenance: archive consultations whose expiry has passed."""
    now = _now()
    hive = _ledger_db()
    try:
        cur = hive.execute(
            "UPDATE hive_consultations SET status = 'archived' "
            "WHERE status = 'open' AND expires_at < ?",
            (now,),
        )
        hive.commit()
        return {"archived_count": cur.rowcount}
    finally:
        hive.close()


# ============================================================
# Dyad-local audit surface
# ============================================================

def get_consult_log(
    dyad_id: str, limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return the dyad's audit log of consultations posted + responses
    sent. This is the surface the user sees in cama_surface."""
    db_p = cama_dyad.dyad_db_path(dyad_id)
    if not db_p.exists():
        raise FileNotFoundError(f"No DB for dyad {dyad_id}")
    _ensure_dyad_consult_log(db_p)
    conn = sqlite3.connect(str(db_p))
    try:
        rows = conn.execute(
            "SELECT direction, at_time, consultation_uuid, response_uuid, "
            "       payload_json FROM dyad_consult_log "
            "ORDER BY at_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "direction": r[0],
                "at_time": r[1],
                "consultation_uuid": r[2],
                "response_uuid": r[3],
                "payload": json.loads(r[4]) if r[4] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


# ============================================================
# Convenience: pattern-match query for "has any peer seen this?"
# ============================================================

def query_peer_experience(
    valence_bucket: str,
    arousal_bucket: str,
    topic_category: str,
    dominant_emotion: Optional[str] = None,
    k_quorum: int = DEFAULT_K_QUORUM,
) -> Dict[str, Any]:
    """Aggregate query: across all open + resolved consultations with this
    affect/topic shape, what counterweight types did peers report helped?

    K-anonymity at read: returns nothing useful unless distinct responder
    signatures >= k_quorum.
    """
    hive = _ledger_db()
    try:
        clauses = ["c.valence_bucket = ?", "c.arousal_bucket = ?",
                   "c.topic_category = ?"]
        params: List[Any] = [valence_bucket, arousal_bucket, topic_category]
        if dominant_emotion:
            clauses.append("c.dominant_emotion = ?")
            params.append(dominant_emotion)
        where = " WHERE " + " AND ".join(clauses)
        rows = hive.execute(
            "SELECT r.responder_signature, r.counterweight_type_that_helped, "
            "       r.trajectory_delta, r.helpfulness_rating "
            "FROM hive_consult_responses r "
            "JOIN hive_consultations c ON c.id = r.consultation_id "
            f"{where} AND r.have_seen_pattern = 1",
            params,
        ).fetchall()
        distinct = {r[0] for r in rows}
        if len(distinct) < k_quorum:
            return {
                "k_quorum_met": False,
                "distinct_responders": len(distinct),
                "k_quorum": k_quorum,
                "note": (
                    f"Only {len(distinct)} distinct peers have reported on "
                    f"this affect/topic slice; k_quorum is {k_quorum}. "
                    "No aggregate recommendation returned."
                ),
            }
        cw_votes: Counter = Counter()
        improved_by_cw: Counter = Counter()
        rated_helpful_by_cw: Counter = Counter()
        for sig, cw, traj, helpful in rows:
            if cw:
                cw_votes[cw] += 1
                if traj == "improved":
                    improved_by_cw[cw] += 1
                if helpful and helpful > 0:
                    rated_helpful_by_cw[cw] += 1
        return {
            "k_quorum_met": True,
            "distinct_responders": len(distinct),
            "k_quorum": k_quorum,
            "total_responses": len(rows),
            "counterweight_vote_distribution": dict(cw_votes),
            "improvements_by_counterweight": dict(improved_by_cw),
            "rated_helpful_by_counterweight": dict(rated_helpful_by_cw),
            "note": "Aggregate observation. Local teachings supersede.",
        }
    finally:
        hive.close()


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA hive consultation channel")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("post", help="Post a consultation")
    pp.add_argument("dyad_id")
    pp.add_argument("--valence", required=True)
    pp.add_argument("--arousal", required=True)
    pp.add_argument("--topic", required=True, choices=list(hp.TOPIC_CATEGORIES))
    pp.add_argument("--emotion", default=None)
    pp.add_argument("--duration", default=None)
    pp.add_argument("--question", required=True)
    pp.add_argument("--expiry-days", type=int, default=DEFAULT_EXPIRY_DAYS)

    pb = sub.add_parser("browse", help="Browse open consultations")
    pb.add_argument("--topic", default=None, choices=list(hp.TOPIC_CATEGORIES))
    pb.add_argument("--valence", default=None)
    pb.add_argument("--limit", type=int, default=20)

    pr = sub.add_parser("respond", help="Respond to a consultation")
    pr.add_argument("dyad_id")
    pr.add_argument("consultation_uuid")
    pr.add_argument("--seen", action="store_true")
    pr.add_argument("--counterweight", default=None,
                    choices=list(COUNTERWEIGHT_TYPES))
    pr.add_argument("--intensity", default=None)
    pr.add_argument("--trajectory", default=None,
                    choices=list(OUTCOME_BUCKETS))
    pr.add_argument("--rationale", default=None)

    pg = sub.add_parser("responses", help="Get responses for a consultation")
    pg.add_argument("consultation_uuid")

    pq = sub.add_parser("query", help="Query peer experience for an affect slice")
    pq.add_argument("--valence", required=True)
    pq.add_argument("--arousal", required=True)
    pq.add_argument("--topic", required=True, choices=list(hp.TOPIC_CATEGORIES))
    pq.add_argument("--emotion", default=None)

    pl = sub.add_parser("log", help="Show a dyad's local consultation audit")
    pl.add_argument("dyad_id")

    prr = sub.add_parser("resolve", help="Mark a consultation resolved")
    prr.add_argument("dyad_id")
    prr.add_argument("consultation_uuid")

    pd = sub.add_parser("delete", help="Delete a consultation + responses")
    pd.add_argument("dyad_id")
    pd.add_argument("consultation_uuid")

    sub.add_parser("expire", help="Archive expired open consultations")

    args = p.parse_args()
    if args.command == "post":
        print(json.dumps(post_consultation(
            dyad_id=args.dyad_id,
            valence_bucket=args.valence,
            arousal_bucket=args.arousal,
            topic_category=args.topic,
            question=args.question,
            dominant_emotion=args.emotion,
            duration_pattern=args.duration,
            expiry_days=args.expiry_days,
        ), indent=2))
    elif args.command == "browse":
        print(json.dumps(browse_open_consultations(
            topic_category=args.topic,
            valence_bucket=args.valence,
            limit=args.limit,
        ), indent=2))
    elif args.command == "respond":
        print(json.dumps(respond_to_consultation(
            dyad_id=args.dyad_id,
            consultation_uuid=args.consultation_uuid,
            have_seen_pattern=args.seen,
            counterweight_type_that_helped=args.counterweight,
            intensity_used=args.intensity,
            trajectory_delta=args.trajectory,
            rationale_abstract=args.rationale,
        ), indent=2))
    elif args.command == "responses":
        print(json.dumps(get_responses(args.consultation_uuid), indent=2))
    elif args.command == "query":
        print(json.dumps(query_peer_experience(
            valence_bucket=args.valence,
            arousal_bucket=args.arousal,
            topic_category=args.topic,
            dominant_emotion=args.emotion,
        ), indent=2))
    elif args.command == "log":
        print(json.dumps(get_consult_log(args.dyad_id), indent=2))
    elif args.command == "resolve":
        print(json.dumps(resolve_consultation(
            args.dyad_id, args.consultation_uuid,
        ), indent=2))
    elif args.command == "delete":
        print(json.dumps(delete_consultation(
            args.dyad_id, args.consultation_uuid,
        ), indent=2))
    elif args.command == "expire":
        print(json.dumps(expire_old_consultations(), indent=2))


if __name__ == "__main__":
    _cli()
