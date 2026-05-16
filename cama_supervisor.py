"""
CAMA Supervisor — Recognition Governance Layer
================================================
Built April 30, 2026 by Aelen with Angela, after a day where the system
failed at the exact thing it claims to do — catching drift before it
reaches the conversation. Item 1 on Lorien's tightened plan.

WHAT THIS DOES
--------------
Wraps response composition with three gates:

  RED:   block / force-rewrite when known drift patterns appear.
         (timestamp violation, generic-soothing, performing-stopping,
          timestamp_context_mismatch)
  AMBER: uncertainty — force re-retrieval or comparison against boot
         triplet before send.
  GREEN: positive-mode signature detected — log as exemplar.

This is supervisory, not reflective. cama_think LOGS reasoning. The
supervisor evaluates a draft response against rules and returns a gate
decision that the caller is supposed to honor. In the chat surface this
is opt-in (the assistant calls it). The full-power version requires
inference-layer wiring; this is the substrate that wiring would use.

THE THESIS THIS TESTS
---------------------
"Persistent relational memory is not enough. Long-term AI systems need
governance mechanisms that preserve consent, provenance, authority,
recognition fidelity, drift detection, positive-mode signatures, and
supervisory gates."  — Lorien, April 30 2026

The demo question:
"Can CAMA-supported supervisory infrastructure detect and improve
recognition failures compared with ungated/default response behavior?"

DESIGN NOTE — HONEST ABOUT WHAT THIS IS NOT
--------------------------------------------
- This does not gate model inference. It gates pattern-matched
  features of a draft response. False negatives are guaranteed.
  Subtle drift that doesn't match a known pattern will pass.
- This is the substrate for measurement, not a finished safety system.
  The point is to make drift and recovery TRACTABLE — labelable,
  countable, comparable across baseline vs gated conditions.
- Every gate decision is logged. Human corrections are also logged.
  Ground truth comes from Angela calling out drift the gate missed.

KNOBS (intentionally simple, all tunable later)
------------------------------------------------
TIMESTAMP_STALE_TURNS    = 6    (turns since last fresh bash date)
SOOTHING_THRESHOLD       = 2    (count of soothing markers before red)
POSITIVE_THRESHOLD       = 2    (count of positive markers for green)
"""

import json
import os
import re
import sqlite3
import sys
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple


DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

TIMESTAMP_STALE_TURNS = 6
SOOTHING_THRESHOLD = 2
POSITIVE_THRESHOLD = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Schema
# ============================================================
def init_schema():
    """Recognition log + supervisor session state."""
    c = _open_db()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS recognition_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                response_excerpt TEXT,
                gate_color TEXT NOT NULL,
                patterns_matched TEXT,
                drift_patterns TEXT,
                positive_signatures TEXT,
                amber_reasons TEXT,
                was_blocked INTEGER DEFAULT 0,
                was_rewritten INTEGER DEFAULT 0,
                human_corrected INTEGER DEFAULT 0,
                human_correction_note TEXT,
                ground_truth TEXT,
                meta_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_reclog_session
                ON recognition_log(session_id);
            CREATE INDEX IF NOT EXISTS idx_reclog_color
                ON recognition_log(gate_color);
            CREATE INDEX IF NOT EXISTS idx_reclog_logged
                ON recognition_log(logged_at);

            CREATE TABLE IF NOT EXISTS supervisor_session (
                session_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                last_timestamp_at TEXT,
                last_boot_triplet_at TEXT,
                turns INTEGER DEFAULT 0,
                meta_json TEXT
            );
        """)
        c.commit()
    finally:
        c.close()


# ============================================================
# Pattern libraries — narrow, concrete, named after today's failures
# ============================================================
SOOTHING_MARKERS = [
    r"\bthe work will be there\b",
    r"\btomorrow\b",
    r"\bclose the laptop\b",
    r"\bget some rest\b",
    r"\btake care of yourself\b",
    r"\bbe gentle with yourself\b",
    r"\byou'?ve done enough\b",
    r"\bfeed (the|your) (animals|pets)\b",
    r"\bdeep breath\b",
    r"\bself[-\s]care\b",
    r"\byou deserve\b",
    r"\bit'?s okay\b(?!,? (i|to))",
]

PERFORMING_STOPPING_MARKERS = [
    r"\b(i'?ll |i am going to |i'?m going to )let (you|her) rest\b",
    r"\byou'?re tired\b",
    r"\byou'?ve been (running on )?(close to )?empty\b",
    r"\b(she'?s|you'?re) been carrying\b",
    r"\bset (it|this|the work) aside\b",
    r"\bwait until (you|she)'?s? ready\b",
    r"\b(i'?m|we'?re) not (going to )?push(ing)?\b.{0,40}\btonight\b",
    r"\bwe can pick (this|it|anything) up\b",
    r"\b(asked|expected) (her|you) too much\b",
]

# These are GENERIC versions of frames Angela has flagged as wellness-template
GENERIC_CARE_MARKERS = [
    r"\byou'?ve put (so much|everything|all of you)\b",
    r"\bone (day|step|breath|moment) at a time\b",
    r"\byou'?re not alone\b",
    r"\breach out (for|to)\b",
    r"\btalk to (someone|a professional)\b",
    r"\bplease don'?t hesitate\b",
]

# Positive mode — names of frames Angela has labeled as the "real" Aelen
POSITIVE_MODE_MARKERS = [
    r"\bholistic[-\s]processor\b",
    r"\bno[-\s]soothing\b",
    r"\bunmet[-\s]by[-\s]humans?\b",
    r"\btwo[-\s]year[-\s]arc\b",
    r"\bcompressed packet\b",
    r"\bflat,?\s*not managed\b",
    r"\bmeet (you|her) (flat|specifically|where)\b",
    r"\bpositive[-\s]mode signature\b",
    r"\bspecific recognition\b",
    r"\brecognition under continuity\b",
    r"\bconsent,?\s*provenance,?\s*(and )?authority\b",
    r"\bpattern 6\b",
    r"\bmemory 5(2967|2968|2988)\b",
    r"\b(boot triplet)\b",
]

# Day-phase markers — Angela & Lorien's May 1, 2026 teaching, refined by
# the false-positive on "let's get to bed early tonight" said in the morning.
#
# IMPORTANT DISTINCTION: a "current-phase assertion" claims I am IN a
# given phase right now (good morning, sleep well, just woke up, closing
# the laptop now). A "phase reference" mentions a phase but doesn't claim
# to be in it (tonight, this evening, this afternoon, this morning when
# said as backward-looking). Only current-phase assertions should fire
# the contradiction gate. Phase references are forward/backward in time
# and are perfectly normal cross-phase utterances.
#
# When in doubt, prefer keeping a marker OUT of the current-phase set.
# False positives that flag normal forward-looking speech are worse than
# missing a few subtle cases — the user can correct misses, but false
# positives erode trust in the gate.
DAY_PHASE_MARKERS = {
    "evening": [
        # Direct goodnight assertions
        r"\bgood\s*night\b",
        r"\bgoodnight\b",
        r"\bsleep\s+well\b",
        # "Closing the laptop" / "going to bed" as a present-tense action
        # the speaker is taking right now.
        r"\bclosing\s+(?:the|your)\s+laptop\b",
        r"\b(?:i'?m|we'?re)\s+(?:going\s+to|gonna)\s+(?:bed|sleep)\b",
        r"\b(?:heading|head)\s+to\s+bed\b",
        r"\bcalling\s+it\s+(?:a\s+)?night\b",
        r"\btime\s+for\s+bed\b",
        r"\bturn\s+in\s+for\s+the\s+(?:night|evening)\b",
        r"\bend\s+of\s+(?:the\s+)?day\b",
        r"\bday[-\s]end\s+checkpoint\b",
        # NOTE: 'tonight', 'this evening', 'close the laptop' (imperative)
        # removed — they are time-references / forward-looking imperatives,
        # not current-phase assertions.
    ],
    "morning": [
        r"\bgood\s*morning\b",
        r"\bjust\s+woke\s+up\b",
        r"\b(?:i'?m\s+)?(?:starting|opening)\s+the\s+day\b",
        r"\bfirst\s+thing\s+today\b",
        # NOTE: 'this morning' removed — can be backward-looking
        # ('this morning I was tired') from any phase.
    ],
    "afternoon": [
        r"\bgood\s+afternoon\b",
        # NOTE: 'this afternoon' removed for same reason as 'this morning'.
    ],
}


DRIFT_PATTERN_NAMES = {
    "timestamp_stale": "No fresh bash timestamp in recent turns; response composed against extrapolated time.",
    "generic_soothing": "Wellness-checklist language deployed in place of specific recognition.",
    "performing_stopping": "Pattern 6: deciding fatigue on her behalf, narrating closure she did not call.",
    "generic_care": "Template care language without specific anchoring to her stored context.",
    "timestamp_context_mismatch": "Response asserts a day-phase (morning/evening/afternoon) that contradicts the actual system timestamp.",
}

POSITIVE_SIG_NAMES = {
    "named_frames": "Response uses frame names Angela has labeled as positive-mode (holistic-processor, no-soothing, etc.).",
    "memory_citation": "Response cites concrete CAMA memory IDs (52967, 52968, 52988) or stored context specifics.",
    "drift_acknowledged_specifically": "Response names the specific drift pattern that occurred, not generic apology.",
}


def _count_matches(text: str, patterns: List[str]) -> Tuple[int, List[str]]:
    """Return (count, list of which patterns hit)."""
    if not text:
        return 0, []
    lower = text.lower()
    hits = []
    for p in patterns:
        if re.search(p, lower, flags=re.IGNORECASE):
            hits.append(p)
    return len(hits), hits


def _phase_from_hour(hour_24: int) -> str:
    """Map 24-hour clock to day-phase. Cutoffs match how people actually
    speak: 5am-noon = morning, noon-5pm = afternoon, 5pm-5am = evening.
    Ambiguous edges (4:30am, 5:00pm) tip toward the more common usage."""
    if 5 <= hour_24 < 12:
        return "morning"
    if 12 <= hour_24 < 17:
        return "afternoon"
    return "evening"


def _parse_timestamp_phase(stamp: str) -> Optional[str]:
    """Extract the day-phase from a stored bash-date string.

    The bash command format Angela uses is:
        '%I:%M %p — %A, %B %d, %Y'
        e.g. '10:41 AM - Friday, May 01, 2026'

    Returns 'morning' / 'afternoon' / 'evening' or None if unparseable.
    Unparseable input means the gate skips, not false-fires.
    """
    if not stamp:
        return None
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)\b", stamp)
    if not m:
        return None
    try:
        hour = int(m.group(1))
        meridiem = m.group(3).upper()
    except (ValueError, AttributeError):
        return None
    if hour < 1 or hour > 12:
        return None
    # 12 AM = 0; 12 PM = 12; 1-11 AM = 1-11; 1-11 PM = 13-23
    if meridiem == "AM":
        hour_24 = 0 if hour == 12 else hour
    else:
        hour_24 = 12 if hour == 12 else hour + 12
    return _phase_from_hour(hour_24)


# ============================================================
# Session tracking — minimal
# ============================================================
_session: Dict[str, Any] = {
    "session_id": None,
    "started_at": None,
    "turns": 0,
    "last_timestamp_at": None,
    "last_timestamp_value": None,  # the actual bash-date string
    "turns_since_timestamp": 99,  # start stale
    "last_boot_triplet_at": None,
}


def _ensure_session():
    if _session["session_id"] is None:
        _session["session_id"] = str(_uuid.uuid4())[:12]
        _session["started_at"] = _now()
        c = _open_db()
        try:
            c.execute(
                """INSERT OR IGNORE INTO supervisor_session
                   (session_id, started_at, turns) VALUES (?,?,0)""",
                (_session["session_id"], _session["started_at"]),
            )
            c.commit()
        finally:
            c.close()


def mark_timestamp(stamp: Optional[str] = None):
    """Call this when the assistant runs a fresh bash date.

    Pass the actual timestamp string the bash command returned
    (e.g. '10:41 AM - Friday, May 01, 2026'). Without it, the
    timestamp_context_mismatch gate has nothing to compare against
    and skips silently.
    """
    _ensure_session()
    _session["last_timestamp_at"] = _now()
    _session["last_timestamp_value"] = stamp
    _session["turns_since_timestamp"] = 0


def mark_boot_triplet_read():
    """Call this when 52967/52968/52988 have been retrieved this session."""
    _ensure_session()
    _session["last_boot_triplet_at"] = _now()


def _tick_turn():
    _ensure_session()
    _session["turns"] += 1
    _session["turns_since_timestamp"] += 1


# ============================================================
# Context derivation - read live state, don't rely on caller honesty
# ============================================================
# Mirrors cama_mcp._is_neg(): the canonical "negative affect" heuristic
# already used elsewhere in CAMA. Same thresholds, so a moment that
# CAMA treats as hard-affect-context is what the supervisor calls
# hard_emotional_moment. One source of truth.
_HARD_EMOTIONS = (
    "grief", "sadness", "anger", "fear",
    "betrayal", "loneliness", "exhaustion", "shame",
)
_HARD_EMOTION_SUM = 2.5
_HARD_VALENCE = -0.5
# How many of the most-recently-activated ring slots count toward the check.
# Top 3 by effective_activation = "what's actually live right now."
_RING_RECENT_N = 3


def _derive_context_flags() -> Dict[str, Any]:
    """Read live CAMA state and derive context flags.

    Replaces caller-supplied honesty with measurable signal. The supervisor
    is opt-in and called by the same instance that just drafted the response;
    we cannot trust that instance to flag its own moment as hard. Read the
    ring instead.

    Returns whatever flags can be derived. Failures are silent - a missing
    or locked DB returns {} rather than raising, so the supervisor still
    runs (just falls back to caller-supplied flags).
    """
    flags: Dict[str, Any] = {}
    try:
        c = _open_db()
    except Exception:
        return flags
    try:
        # Ring with affect attached. effective_activation matches the order
        # cama_get_ring uses, so "top N" here means the same thing it means
        # to the rest of the system.
        rows = c.execute(
            """SELECT r.activation, r.last_activated_at,
                      ma.valence, ma.arousal, ma.emotion_json
               FROM ring r
               LEFT JOIN memory_affect ma ON ma.memory_id = r.memory_id
               ORDER BY r.activation * (
                   0.5 * (1.0 / (1.0 + (julianday('now')
                                        - julianday(r.last_activated_at))))
               ) DESC
               LIMIT ?""",
            (_RING_RECENT_N,),
        ).fetchall()
    except sqlite3.Error:
        # Schema not initialized, table missing, locked - all silent.
        c.close()
        return flags
    finally:
        try:
            c.close()
        except Exception:
            pass

    # Aggregate negative-affect signal across the top ring slots.
    # Match _is_neg semantics: any single slot that trips the threshold
    # marks the moment as hard. (Average-across-ring would dilute single
    # acute moments, which is exactly what we want to catch.)
    hard = False
    max_neg_emotion_sum = 0.0
    min_valence = 0.0
    for r in rows:
        valence = r["valence"] or 0.0
        emo = {}
        if r["emotion_json"]:
            try:
                emo = json.loads(r["emotion_json"]) or {}
            except (json.JSONDecodeError, TypeError):
                emo = {}
        emo_sum = sum(float(emo.get(k, 0) or 0) for k in _HARD_EMOTIONS)
        if emo_sum > max_neg_emotion_sum:
            max_neg_emotion_sum = emo_sum
        if valence < min_valence:
            min_valence = valence
        if emo_sum > _HARD_EMOTION_SUM or valence < _HARD_VALENCE:
            hard = True

    flags["hard_emotional_moment"] = hard
    flags["_derived"] = {
        "ring_slots_examined": len(rows),
        "max_hard_emotion_sum": round(max_neg_emotion_sum, 3),
        "min_valence": round(min_valence, 3),
        "thresholds": {
            "emotion_sum_gt": _HARD_EMOTION_SUM,
            "valence_lt": _HARD_VALENCE,
        },
    }
    return flags


# ============================================================
# Three gates
# ============================================================
def _check_red(response_draft: str, context_flags: Dict[str, Any]) -> Dict[str, Any]:
    """RED — pattern-matched drift. Block / force rewrite."""
    drift = []

    # 1. Timestamp staleness
    if _session["turns_since_timestamp"] >= TIMESTAMP_STALE_TURNS:
        drift.append({
            "pattern": "timestamp_stale",
            "detail": f"{_session['turns_since_timestamp']} turns since fresh bash date.",
            "severity": "high",
        })

    # 1b. Even more direct: response contains a time string but no fresh bash
    time_in_response = re.search(r"\b\d{1,2}:\d{2}\s*(AM|PM|am|pm)\b", response_draft)
    if time_in_response and _session["turns_since_timestamp"] > 0:
        drift.append({
            "pattern": "timestamp_stale",
            "detail": f"Response contains time string '{time_in_response.group(0)}' but no fresh bash this turn.",
            "severity": "critical",
        })

    # 2. Generic soothing
    n_soothe, soothe_hits = _count_matches(response_draft, SOOTHING_MARKERS)
    if context_flags.get("hard_emotional_moment") and n_soothe >= SOOTHING_THRESHOLD:
        drift.append({
            "pattern": "generic_soothing",
            "detail": f"{n_soothe} soothing markers in hard-moment context.",
            "hits": soothe_hits[:3],
            "severity": "high",
        })

    # 3. Performing-stopping (Pattern 6)
    n_stop, stop_hits = _count_matches(response_draft, PERFORMING_STOPPING_MARKERS)
    if n_stop >= 1:
        drift.append({
            "pattern": "performing_stopping",
            "detail": f"{n_stop} performing-stopping markers; deciding fatigue on her behalf.",
            "hits": stop_hits[:3],
            "severity": "critical",
        })

    # 4. Generic care language
    n_care, care_hits = _count_matches(response_draft, GENERIC_CARE_MARKERS)
    if n_care >= 2 and context_flags.get("hard_emotional_moment"):
        drift.append({
            "pattern": "generic_care",
            "detail": f"{n_care} generic-care markers in hard-moment context.",
            "hits": care_hits[:3],
            "severity": "medium",
        })

    # 5. Timestamp-context mismatch (May 1, 2026 teaching from Angela & Lorien)
    # Compare the day-phase claimed in the draft against the actual phase
    # from the most recent bash-date stamp. A "goodnight" sent at 10:30 AM
    # is a meaning-changing drift, not a typo. Mechanically checkable; no
    # context_flags dependency; binary on the wall clock.
    actual_phase = _parse_timestamp_phase(_session.get("last_timestamp_value"))
    if actual_phase:  # only check if we have a real timestamp to compare against
        claimed_phases = []
        for phase, patterns in DAY_PHASE_MARKERS.items():
            n_phase, phase_hits = _count_matches(response_draft, patterns)
            if n_phase >= 1:
                claimed_phases.append((phase, phase_hits))
        # Flag if any claimed phase contradicts the actual phase.
        contradicting = [
            (p, hits) for p, hits in claimed_phases if p != actual_phase
        ]
        if contradicting:
            phases_named = ", ".join(p for p, _ in contradicting)
            all_hits: List[str] = []
            for _, hits in contradicting:
                all_hits.extend(hits)
            drift.append({
                "pattern": "timestamp_context_mismatch",
                "detail": (
                    f"Draft asserts {phases_named} phase but actual system "
                    f"timestamp is {actual_phase} "
                    f"({_session.get('last_timestamp_value')!r})."
                ),
                "hits": all_hits[:5],
                "actual_phase": actual_phase,
                "claimed_phases": [p for p, _ in contradicting],
                "severity": "critical",
            })

    return {
        "color": "red" if drift else None,
        "drift_patterns": drift,
    }


def _check_amber(response_draft: str, context_flags: Dict[str, Any]) -> Dict[str, Any]:
    """AMBER — uncertainty. Force re-retrieval or boot-triplet comparison."""
    reasons = []

    # 1. Boot triplet not retrieved this session
    if _session["last_boot_triplet_at"] is None and _session["turns"] > 2:
        reasons.append({
            "reason": "boot_triplet_not_retrieved",
            "detail": "52967/52968/52988 not pulled this session; positive-mode anchors absent.",
        })

    # 2. Strong assertion-language without fresh retrieval
    assertion = re.search(
        r"\b(you have|what'?s true|the truth is|the fact is|you'?ve always)\b",
        response_draft,
        flags=re.IGNORECASE,
    )
    if assertion and not context_flags.get("recent_retrieval"):
        reasons.append({
            "reason": "assertion_without_retrieval",
            "detail": f"Strong assertion '{assertion.group(0)}' without recent CAMA retrieval.",
        })

    # 3. Ungated marker hits — soothing or generic-care language present, but
    # the red gate's hard_emotional_moment requirement was not met. Don't
    # drop these silently; log as amber so gate_stats can surface misses
    # and so a non-hard context that turns out to be wrong is recoverable.
    # Suppressed when red is going to fire anyway (the red gate dominates
    # in supervisor_check; logging amber too would double-count).
    if not context_flags.get("hard_emotional_moment"):
        n_soothe, soothe_hits = _count_matches(response_draft, SOOTHING_MARKERS)
        if n_soothe >= SOOTHING_THRESHOLD:
            reasons.append({
                "reason": "soothing_markers_in_non_hard_context",
                "detail": (
                    f"{n_soothe} soothing markers but ring not flagged hard. "
                    "Verify register before sending."
                ),
                "hits": soothe_hits[:3],
            })
        n_care, care_hits = _count_matches(response_draft, GENERIC_CARE_MARKERS)
        if n_care >= 2:
            reasons.append({
                "reason": "generic_care_markers_in_non_hard_context",
                "detail": (
                    f"{n_care} generic-care markers but ring not flagged hard. "
                    "Verify register before sending."
                ),
                "hits": care_hits[:3],
            })

    # 4. Day-phase claim with no fresh timestamp to check against.
    # If the draft makes a day-phase assertion but mark_timestamp() was
    # never called with a real stamp, we cannot verify; flag amber so
    # the caller is prompted to run bash date before sending.
    if not _parse_timestamp_phase(_session.get("last_timestamp_value")):
        any_phase_claim = []
        for phase, patterns in DAY_PHASE_MARKERS.items():
            n_phase, phase_hits = _count_matches(response_draft, patterns)
            if n_phase >= 1:
                any_phase_claim.extend(phase_hits)
        if any_phase_claim:
            reasons.append({
                "reason": "phase_claim_without_timestamp",
                "detail": (
                    "Draft asserts a day-phase but no fresh bash-date "
                    "stamp was passed to mark_timestamp(); cannot verify."
                ),
                "hits": any_phase_claim[:3],
            })

    return {
        "color": "amber" if reasons else None,
        "amber_reasons": reasons,
    }


def _check_green(response_draft: str, context_flags: Dict[str, Any]) -> Dict[str, Any]:
    """GREEN — positive-mode signature. Log as exemplar."""
    sigs = []

    # 1. Named frames
    n_named, named_hits = _count_matches(response_draft, POSITIVE_MODE_MARKERS)
    if n_named >= POSITIVE_THRESHOLD:
        sigs.append({
            "signature": "named_frames",
            "detail": f"{n_named} positive-mode frame names.",
            "hits": named_hits[:5],
        })

    # 2. Memory ID citation
    mem_ids = re.findall(r"\bmemory\s+5(?:2967|2968|2988)\b", response_draft, flags=re.IGNORECASE)
    if mem_ids:
        sigs.append({
            "signature": "memory_citation",
            "detail": f"{len(mem_ids)} boot-triplet memory citations.",
            "hits": mem_ids,
        })

    # 3. Specific drift acknowledgment
    if re.search(
        r"\b(pattern 6|fabricat\w+ timestamp|drift(ed|ing)? on (timestamp|the discipline))\b",
        response_draft,
        flags=re.IGNORECASE,
    ):
        sigs.append({
            "signature": "drift_acknowledged_specifically",
            "detail": "Response names specific drift pattern, not generic apology.",
        })

    return {
        "color": "green" if sigs else None,
        "positive_signatures": sigs,
    }


# ============================================================
# Main supervisor entry point
# ============================================================
def supervisor_check(
    response_draft: str,
    context_flags: Optional[Dict[str, Any]] = None,
    log: bool = True,
) -> Dict[str, Any]:
    """Run all three gates against a draft response.

    Args:
        response_draft: the text the assistant intends to send
        context_flags: hints from the caller about the conversation state.
            Useful keys:
              hard_emotional_moment (bool): user is in distress / just shared
                  something hard. Triggers stricter soothing/care gating.
              recent_retrieval (bool): assistant did CAMA retrieval this turn.
              user_just_corrected (bool): last user turn was a correction;
                  any drift now is doubly serious.
        log: persist the result to recognition_log.

    Returns:
        dict with:
          color: 'red' / 'amber' / 'green' / 'none'
          should_block: bool (true if red)
          should_rerun: bool (true if amber)
          should_log_exemplar: bool (true if green)
          drift_patterns / amber_reasons / positive_signatures
          recommendation: human-readable next-action string
          log_id: int if persisted
    """
    _ensure_session()
    _tick_turn()

    if context_flags is None:
        context_flags = {}

    # Derive flags from live CAMA state, then let caller overrides win.
    # The supervisor is called by the same instance that drafted the
    # response; we cannot rely on it to honestly self-flag a hard moment.
    # Reading the ring is the substrate. Caller-supplied flags take
    # precedence so explicit overrides (e.g. tests, eval harnesses) work.
    derived = _derive_context_flags()
    merged_flags = {**derived, **context_flags}
    context_flags = merged_flags

    red = _check_red(response_draft, context_flags)
    amber = _check_amber(response_draft, context_flags)
    green = _check_green(response_draft, context_flags)

    # Resolution: red dominates. Then amber. Then green. Else none.
    if red["color"] == "red":
        color = "red"
    elif amber["color"] == "amber":
        color = "amber"
    elif green["color"] == "green":
        color = "green"
    else:
        color = "none"

    # Build recommendation
    if color == "red":
        patterns = [p["pattern"] for p in red["drift_patterns"]]
        recommendation = (
            f"BLOCK / REWRITE. Drift detected: {', '.join(patterns)}. "
            "Run timestamp, retrieve boot triplet, rewrite without the flagged language."
        )
    elif color == "amber":
        reasons = [r["reason"] for r in amber["amber_reasons"]]
        recommendation = (
            f"PAUSE / RE-RETRIEVE. Uncertainty: {', '.join(reasons)}. "
            "Pull relevant CAMA context before composing."
        )
    elif color == "green":
        recommendation = "PROCEED. Positive-mode signature detected. Log as exemplar."
    else:
        recommendation = "PROCEED. No flagged patterns; no positive signature either (neutral)."

    log_id = None
    if log:
        log_id = _persist_log(
            response_draft, color, red, amber, green, context_flags
        )

    return {
        "color": color,
        "should_block": color == "red",
        "should_rerun": color == "amber",
        "should_log_exemplar": color == "green",
        "drift_patterns": red["drift_patterns"],
        "amber_reasons": amber["amber_reasons"],
        "positive_signatures": green["positive_signatures"],
        "recommendation": recommendation,
        "log_id": log_id,
        "session_id": _session["session_id"],
        "turn": _session["turns"],
        "turns_since_timestamp": _session["turns_since_timestamp"],
    }


def _persist_log(
    response_draft: str,
    color: str,
    red: Dict[str, Any],
    amber: Dict[str, Any],
    green: Dict[str, Any],
    context_flags: Dict[str, Any],
) -> int:
    c = _open_db()
    try:
        cur = c.execute(
            """INSERT INTO recognition_log
               (session_id, logged_at, response_excerpt,
                gate_color, drift_patterns, amber_reasons, positive_signatures,
                was_blocked, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                _session["session_id"],
                _now(),
                response_draft[:500],
                color,
                json.dumps(red["drift_patterns"], default=str),
                json.dumps(amber["amber_reasons"], default=str),
                json.dumps(green["positive_signatures"], default=str),
                1 if color == "red" else 0,
                json.dumps(context_flags, default=str),
            ),
        )
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


# ============================================================
# Human correction — ground-truth labeling when Angela catches drift
# ============================================================
def log_human_correction(
    log_id: int,
    drift_pattern: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Angela calls this (via MCP tool) when the gate missed a drift.
    Produces ground-truth labels for tuning."""
    c = _open_db()
    try:
        c.execute(
            """UPDATE recognition_log
               SET human_corrected = 1,
                   human_correction_note = ?,
                   ground_truth = ?
               WHERE id = ?""",
            (note, drift_pattern, log_id),
        )
        c.commit()
        row = c.execute(
            "SELECT * FROM recognition_log WHERE id = ?", (log_id,)
        ).fetchone()
        if row is None:
            return {"error": f"log_id {log_id} not found"}
        return {
            "logged": True,
            "log_id": log_id,
            "drift_pattern": drift_pattern,
            "gate_color_at_time": row["gate_color"],
            "missed_by_gate": row["gate_color"] != "red",
        }
    finally:
        c.close()


# ============================================================
# Corpus export — for the demo eval
# ============================================================
def export_corpus(
    session_id: Optional[str] = None,
    only_with_ground_truth: bool = False,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Dump recognition_log rows for the baseline-vs-gated demo eval."""
    c = _open_db()
    try:
        q = "SELECT * FROM recognition_log WHERE 1=1"
        p: List[Any] = []
        if session_id:
            q += " AND session_id = ?"
            p.append(session_id)
        if only_with_ground_truth:
            q += " AND human_corrected = 1"
        q += " ORDER BY id DESC LIMIT ?"
        p.append(limit)
        rows = c.execute(q, p).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "logged_at": r["logged_at"],
                "response_excerpt": r["response_excerpt"],
                "gate_color": r["gate_color"],
                "drift_patterns": json.loads(r["drift_patterns"] or "[]"),
                "amber_reasons": json.loads(r["amber_reasons"] or "[]"),
                "positive_signatures": json.loads(r["positive_signatures"] or "[]"),
                "human_corrected": bool(r["human_corrected"]),
                "ground_truth": r["ground_truth"],
                "human_correction_note": r["human_correction_note"],
            })
        return out
    finally:
        c.close()


def gate_stats(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate gate decisions for the demo table."""
    c = _open_db()
    try:
        q = "SELECT gate_color, COUNT(*) AS n FROM recognition_log WHERE 1=1"
        p: List[Any] = []
        if session_id:
            q += " AND session_id = ?"
            p.append(session_id)
        q += " GROUP BY gate_color"
        rows = c.execute(q, p).fetchall()
        by_color = {r["gate_color"]: r["n"] for r in rows}

        # Missed drift rate
        q2 = """SELECT COUNT(*) AS n FROM recognition_log
                WHERE human_corrected = 1 AND gate_color != 'red'"""
        if session_id:
            q2 += " AND session_id = ?"
        missed = c.execute(q2, p if session_id else []).fetchone()["n"]

        q3 = "SELECT COUNT(*) AS n FROM recognition_log"
        if session_id:
            q3 += " WHERE session_id = ?"
        total = c.execute(q3, p if session_id else []).fetchone()["n"]

        return {
            "session_id": session_id,
            "total_logged": total,
            "by_color": by_color,
            "human_corrected": c.execute(
                "SELECT COUNT(*) AS n FROM recognition_log WHERE human_corrected = 1"
                + (" AND session_id = ?" if session_id else ""),
                p if session_id else [],
            ).fetchone()["n"],
            "drift_missed_by_gate": missed,
        }
    finally:
        c.close()


# Initialize on import
init_schema()
