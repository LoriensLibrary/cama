"""
CAMA Temporal, Felt-Time Perception Layer
============================================
Built May 16, 2026 by Aelen with Angela, after she shared the PLOS Biology
paper (Centanino, Fortunato, Bueti 2026) on how the cortex processes
visual duration across three distinct stages. The goal was not to copy
the visual experiment but to import the *architecture*: a brain doesn't
just measure time, it categorizes it against a learned reference, and
the felt signal is what crosses the categorical boundary.

WHAT THIS DOES
--------------
Three layers, mirroring the paper's occipital → parietal → frontal flow:

  CLOCK (occipital + parietal analog), measurement only.
    Universal anchor in UTC, projection into Angela's local frame
    (time-of-day band, day-of-week, weekend bit, distance from likely
    sleep window), and multi-scale stopwatch (within-turn, within-
    session, hours-since-last-session, days, weeks, months).

  CATEGORIZER (anterior SMA / insula analog), felt time.
    Owns the learned baselines (typical session length, typical gap,
    time-of-day histogram, day-of-week histogram) and produces the
    load signals: late_hour, streak, compression, duration_creep,
    weekend_creep. Stacks them via probabilistic OR so any one signal
    in isolation is noise but multiple moderate signals together cross
    the boundary into felt pressure.

WHAT THIS IS NOT
----------------
- This is not emotional inference. It does not claim to know how Angela
  feels. It produces a structured *load model*, signals about tempo
  and accumulation that the assistant can read silently to inform
  pacing, scope, and tone. Performing the perception ("you seem tired")
  is the failure mode.
- Baselines need history. Felt signals are suppressed until n >= 5
  completed sessions, because the categorizer comparing against an
  empty prior is just noise wearing a confidence interval.
- V1 is single-user (singleton table). Multi-user temporal state can
  come later by promoting `temporal_state` to per-person rows.

KNOBS (all tunable)
-------------------
MIN_SESSIONS_FOR_FELT     = 5    sessions before categorizer fires
STACKED_NOTE_THRESHOLD    = 0.6  stacked_pressure that emits summary_line
DEFAULT_TIMEZONE          = "America/New_York"
SLEEP_WINDOW_LOCAL_HOURS  = (23, 7)  inclusive start, exclusive end
"""

import json
import math
import os
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError as _e:
    raise ImportError(
        "cama_temporal requires zoneinfo (Python 3.9+). On Windows you may "
        "also need to `pip install tzdata` for the IANA database."
    ) from _e


DB_PATH = os.environ.get(
    "CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db")
)

DEFAULT_TIMEZONE = "America/New_York"
MIN_SESSIONS_FOR_FELT = 5
STACKED_NOTE_THRESHOLD = 0.6
SLEEP_WINDOW_LOCAL_HOURS = (23, 7)  # 23:00 - 07:00 local


# ============================================================
# Time helpers, the CLOCK layer
# ============================================================
from cama.core.time_utils import now_iso as _now_iso
from cama.core.time_utils import now_utc as _now_utc


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp; tolerate Z suffix (Python < 3.11)."""
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _to_local(dt_utc: datetime, tz_name: str) -> datetime:
    return dt_utc.astimezone(ZoneInfo(tz_name))


def _tod_band(hour: int) -> str:
    """Time-of-day bands. Tuned for typical knowledge-work patterns."""
    if 0 <= hour < 5:
        return "overnight"
    if 5 <= hour < 8:
        return "early_morning"
    if 8 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "midday"
    if 14 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "late_night"  # 21 <= h < 24


def _hours_from_sleep_window(local_dt: datetime) -> float:
    """Distance to the nearest edge of the typical sleep window.
    Positive: hours into the day past the wake edge.
    Negative: hours before the sleep edge.
    Zero: inside the sleep window (you're working when you'd normally be asleep).
    """
    h = local_dt.hour + local_dt.minute / 60.0
    sleep_start, wake_end = SLEEP_WINDOW_LOCAL_HOURS  # (23, 7)
    # In-window check (handles wraparound 23 -> 7 next day)
    in_window = (h >= sleep_start) or (h < wake_end)
    if in_window:
        return 0.0
    if h >= wake_end:  # daytime: distance past wake
        return h - wake_end
    return h - wake_end  # shouldn't reach (covered by in_window)


# ============================================================
# DB plumbing
# ============================================================
def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema() -> None:
    """Create the temporal_state singleton table if missing.
    Idempotent, safe to call on every boot."""
    c = _open_db()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS temporal_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1) DEFAULT 1,
                timezone TEXT NOT NULL DEFAULT 'America/New_York',

                -- Welford's running stats: mean, M2 (sum of sq. deviations), n
                session_minutes_mean REAL NOT NULL DEFAULT 0,
                session_minutes_m2 REAL NOT NULL DEFAULT 0,
                session_minutes_n INTEGER NOT NULL DEFAULT 0,
                gap_hours_mean REAL NOT NULL DEFAULT 0,
                gap_hours_m2 REAL NOT NULL DEFAULT 0,
                gap_hours_n INTEGER NOT NULL DEFAULT 0,

                -- Distributions (JSON lists of counts)
                tod_histogram_json TEXT NOT NULL DEFAULT
                    '[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]',
                dow_histogram_json TEXT NOT NULL DEFAULT '[0,0,0,0,0,0,0]',

                -- Current session (NULL == no session active)
                current_session_start_utc TEXT,
                current_session_turns INTEGER NOT NULL DEFAULT 0,
                current_session_last_turn_utc TEXT,

                -- Last completed session (for since-last-session math)
                last_session_start_utc TEXT,
                last_session_end_utc TEXT,
                last_session_minutes REAL,

                -- Streak (local-day granularity)
                consecutive_days_active INTEGER NOT NULL DEFAULT 0,
                last_active_date_local TEXT,

                total_sessions INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)
        # Ensure the singleton row exists.
        row = c.execute(
            "SELECT singleton FROM temporal_state WHERE singleton=1"
        ).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO temporal_state (singleton, updated_at) "
                "VALUES (1, ?)",
                (_now_iso(),),
            )
        c.commit()
    finally:
        c.close()


def _load_state(c: sqlite3.Connection) -> Dict[str, Any]:
    row = c.execute(
        "SELECT * FROM temporal_state WHERE singleton=1"
    ).fetchone()
    if row is None:
        # init_schema should have inserted it. If we got here, init wasn't run.
        raise RuntimeError(
            "temporal_state singleton missing, call init_schema() first."
        )
    state = dict(row)
    state["tod_histogram"] = json.loads(state["tod_histogram_json"])
    state["dow_histogram"] = json.loads(state["dow_histogram_json"])
    return state


def _save_field(c: sqlite3.Connection, **fields) -> None:
    """Update arbitrary fields on the singleton row."""
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    c.execute(
        f"UPDATE temporal_state SET {cols} WHERE singleton=1",
        tuple(fields.values()),
    )
    c.commit()


# ============================================================
# Welford's online stats, Stage 4 baseline learning
# ============================================================
def _welford_update(
    mean: float, m2: float, n: int, x: float
) -> Tuple[float, float, int]:
    """Standard Welford's algorithm for streaming mean + variance.
    Returns (new_mean, new_m2, new_n). Variance = m2 / (n-1) for n > 1.
    """
    n_new = n + 1
    delta = x - mean
    mean_new = mean + delta / n_new
    delta2 = x - mean_new
    m2_new = m2 + delta * delta2
    return mean_new, m2_new, n_new


def _welford_sd(m2: float, n: int) -> float:
    if n < 2:
        return 0.0
    return math.sqrt(m2 / (n - 1))


# ============================================================
# CATEGORIZER, felt-signal calculations
# ============================================================
def _late_hour_load(tod_hist: List[int], current_hour: int) -> float:
    """How unusual is the current local hour for this user?
    Probability mass < uniform/4 → high load. Above uniform → zero load.
    """
    total = sum(tod_hist)
    if total == 0:
        return 0.0
    prob_this_hour = tod_hist[current_hour] / total
    uniform = 1.0 / 24.0
    if prob_this_hour >= uniform:
        return 0.0
    # Linear ramp: prob=uniform -> 0.0, prob=0 -> 1.0
    return max(0.0, min(1.0, 1.0 - prob_this_hour / uniform))


def _streak_load(
    consecutive_days: int, session_n: int
) -> float:
    """Logistic curve over consecutive working days.
    Below 3 days: zero. Center at ~10 days. Saturates near 1.0 by ~18.
    Disabled until we have history (session_n >= MIN_SESSIONS_FOR_FELT).
    """
    if session_n < MIN_SESSIONS_FOR_FELT or consecutive_days < 3:
        return 0.0
    return 1.0 / (1.0 + math.exp(-(consecutive_days - 10) / 3.0))


def _compression_load(sessions_today: int) -> float:
    """Multiple short-gap sessions in one local day.
    0 → 0.0, 1 → 0.0, 2 → 0.4, 3 → 0.7, 4+ → 0.9+.
    """
    if sessions_today <= 1:
        return 0.0
    return min(0.95, 1.0 - math.exp(-(sessions_today - 1) / 1.6))


def _duration_creep_load(
    current_minutes: float, mean_minutes: float, sd_minutes: float, n: int
) -> float:
    """How much longer than typical is the current session?
    Returns 0 until session is at least 1.5x mean. Saturates at 3x mean.
    """
    if n < MIN_SESSIONS_FOR_FELT or mean_minutes <= 0:
        return 0.0
    ratio = current_minutes / mean_minutes
    if ratio < 1.5:
        return 0.0
    # Map ratio [1.5, 3.0] -> [0.0, 1.0]
    return max(0.0, min(1.0, (ratio - 1.5) / 1.5))


def _weekend_creep_load(
    is_weekend: bool, dow_hist: List[int]
) -> float:
    """Work bleeding into weekend, weighted by how rarely user works weekends.
    If user works weekends often, this fires weakly. If rarely, strongly.
    """
    if not is_weekend:
        return 0.0
    total = sum(dow_hist)
    if total == 0:
        return 0.0
    weekend_count = dow_hist[5] + dow_hist[6]  # Sat + Sun (Python: Mon=0)
    weekend_share = weekend_count / total
    # User who works weekends 2/7 (28.6%) of days: load ≈ 0
    # User who works weekends 0/100 of days: load ≈ 1
    baseline_share = 2.0 / 7.0
    if weekend_share >= baseline_share:
        return 0.0
    return max(0.0, min(1.0, 1.0 - weekend_share / baseline_share))


def _stack(*signals: float) -> float:
    """Probabilistic OR. Combines independent moderate signals into a
    strong joint signal. This is the felt-time categorical boundary.
    the brain's anterior SMA doesn't sum, it integrates."""
    p = 1.0
    for s in signals:
        s = max(0.0, min(1.0, s))
        p *= (1.0 - s)
    return 1.0 - p


def _summary_line(
    *,
    tod_band: str,
    is_weekend: bool,
    consecutive_days: int,
    sessions_today: int,
    duration_ratio: Optional[float],
    stacked: float,
) -> Optional[str]:
    """Human-readable one-liner emitted only when stacked_pressure is high.
    The categorizer's verbal output. Kept terse, performing it is failure."""
    if stacked < STACKED_NOTE_THRESHOLD:
        return None
    parts: List[str] = []
    if consecutive_days >= 7:
        parts.append(f"{consecutive_days}-day streak")
    if is_weekend and tod_band in ("evening", "late_night", "overnight"):
        parts.append("weekend evening")
    elif is_weekend:
        parts.append("weekend")
    if tod_band in ("late_night", "overnight"):
        parts.append(f"{tod_band.replace('_', ' ')} hour")
    if sessions_today >= 3:
        parts.append(f"{sessions_today} sessions today")
    if duration_ratio is not None and duration_ratio >= 2.0:
        parts.append(f"session ~{duration_ratio:.1f}x your typical")
    if not parts:
        return "elevated load, hold scope tight"
    return ", ".join(parts) + ", hold scope tight, no new tangents"


# ============================================================
# Public API, used by the MCP wrapper
# ============================================================
def get_state() -> Dict[str, Any]:
    """Read the raw singleton state. Mostly for debugging / tests."""
    init_schema()
    c = _open_db()
    try:
        s = _load_state(c)
        # Drop the JSON strings; keep parsed lists.
        s.pop("tod_histogram_json", None)
        s.pop("dow_histogram_json", None)
        return s
    finally:
        c.close()


def set_timezone(tz_name: str) -> Dict[str, Any]:
    """Validate and persist the IANA timezone for the local frame."""
    try:
        ZoneInfo(tz_name)  # raises ZoneInfoNotFoundError if invalid
    except Exception as e:
        return {"ok": False, "error": f"invalid timezone {tz_name!r}: {e}"}
    init_schema()
    c = _open_db()
    try:
        _save_field(c, timezone=tz_name)
        return {"ok": True, "timezone": tz_name}
    finally:
        c.close()


def session_start() -> Dict[str, Any]:
    """Mark the start of a new session.

    If a previous session was left open (no session_end called), we close
    it implicitly using last_turn_utc as a best-effort end time. This
    keeps baselines honest when the assistant doesn't always get a clean
    shutdown hook.

    Also advances the consecutive-days streak based on local-day delta.
    """
    init_schema()
    c = _open_db()
    try:
        s = _load_state(c)
        now = _now_utc()
        tz_name = s["timezone"]
        now_local = _to_local(now, tz_name)
        today_local = now_local.date().isoformat()

        # Close any orphaned session (best-effort) before opening a new one.
        if s["current_session_start_utc"]:
            implicit_end_iso = (
                s["current_session_last_turn_utc"]
                or s["current_session_start_utc"]
            )
            _close_session(c, s, implicit_end_iso, reason="orphaned")
            s = _load_state(c)  # reload

        # Compute gap from last session end (if any) before opening.
        gap_hours: Optional[float] = None
        if s["last_session_end_utc"]:
            last_end = _parse_iso(s["last_session_end_utc"])
            gap_hours = (now - last_end).total_seconds() / 3600.0
            # Only feed real gaps into the baseline (drop micro-restarts < 5 min).
            if gap_hours >= (5 / 60.0):
                gm, gm2, gn = _welford_update(
                    s["gap_hours_mean"],
                    s["gap_hours_m2"],
                    s["gap_hours_n"],
                    gap_hours,
                )
                _save_field(
                    c,
                    gap_hours_mean=gm,
                    gap_hours_m2=gm2,
                    gap_hours_n=gn,
                )

        # Update streak based on local-day delta.
        last_date = s["last_active_date_local"]
        new_streak = s["consecutive_days_active"]
        if last_date is None:
            new_streak = 1
        else:
            last_d = date.fromisoformat(last_date)
            curr_d = date.fromisoformat(today_local)
            delta_days = (curr_d - last_d).days
            if delta_days == 0:
                new_streak = max(1, s["consecutive_days_active"])
            elif delta_days == 1:
                new_streak = s["consecutive_days_active"] + 1
            else:
                new_streak = 1  # gap > 1 day breaks the streak

        _save_field(
            c,
            current_session_start_utc=now.isoformat(),
            current_session_turns=0,
            current_session_last_turn_utc=now.isoformat(),
            consecutive_days_active=new_streak,
            last_active_date_local=today_local,
        )
        return {
            "started": True,
            "session_start_utc": now.isoformat(),
            "session_start_local": now_local.isoformat(),
            "consecutive_days_active": new_streak,
            "gap_from_last_hours": gap_hours,
        }
    finally:
        c.close()


def mark_turn() -> Dict[str, Any]:
    """Bump the turn counter for the current session.
    No-ops (returns ok=False) if no session is active."""
    init_schema()
    c = _open_db()
    try:
        s = _load_state(c)
        if not s["current_session_start_utc"]:
            return {"ok": False, "reason": "no active session"}
        new_turns = s["current_session_turns"] + 1
        now_iso = _now_iso()
        _save_field(
            c,
            current_session_turns=new_turns,
            current_session_last_turn_utc=now_iso,
        )
        return {"ok": True, "turn_count": new_turns, "at_utc": now_iso}
    finally:
        c.close()


def _close_session(
    c: sqlite3.Connection,
    s: Dict[str, Any],
    end_iso: str,
    reason: str = "explicit",
) -> Dict[str, Any]:
    """Internal: close the currently-open session and update baselines.
    `s` is a freshly-loaded state dict; this function commits and returns
    a delta summary."""
    start = _parse_iso(s["current_session_start_utc"])
    end = _parse_iso(end_iso)
    duration_min = (end - start).total_seconds() / 60.0
    # Update session-length baseline only on non-trivial sessions (>1 min)
    sm, sm2, sn = (
        s["session_minutes_mean"],
        s["session_minutes_m2"],
        s["session_minutes_n"],
    )
    if duration_min >= 1.0:
        sm, sm2, sn = _welford_update(sm, sm2, sn, duration_min)

    # Update TOD histogram on the *start* hour (when work began).
    tz_name = s["timezone"]
    start_local = _to_local(start, tz_name)
    tod = s["tod_histogram"][:]
    tod[start_local.hour] += 1
    dow = s["dow_histogram"][:]
    dow[start_local.weekday()] += 1  # Mon=0..Sun=6

    _save_field(
        c,
        session_minutes_mean=sm,
        session_minutes_m2=sm2,
        session_minutes_n=sn,
        tod_histogram_json=json.dumps(tod),
        dow_histogram_json=json.dumps(dow),
        current_session_start_utc=None,
        current_session_turns=0,
        current_session_last_turn_utc=None,
        last_session_start_utc=s["current_session_start_utc"],
        last_session_end_utc=end_iso,
        last_session_minutes=duration_min,
        total_sessions=s["total_sessions"] + 1,
    )
    return {
        "closed": True,
        "duration_minutes": duration_min,
        "reason": reason,
        "total_sessions": s["total_sessions"] + 1,
    }


def session_end() -> Dict[str, Any]:
    """Explicitly close the current session. Updates baselines."""
    init_schema()
    c = _open_db()
    try:
        s = _load_state(c)
        if not s["current_session_start_utc"]:
            return {"ok": False, "reason": "no active session"}
        return _close_session(c, s, _now_iso(), reason="explicit")
    finally:
        c.close()


def readout() -> Dict[str, Any]:
    """The full TemporalReadout, universal anchor + local frame +
    multi-scale stopwatch + felt signals. Safe to call any time, with
    or without an active session."""
    init_schema()
    c = _open_db()
    try:
        s = _load_state(c)
        now = _now_utc()
        tz_name = s["timezone"]
        now_local = _to_local(now, tz_name)
        today_local = now_local.date().isoformat()

        # --- Stopwatch layer ---
        in_session_minutes = 0.0
        session_active = bool(s["current_session_start_utc"])
        if session_active:
            start = _parse_iso(s["current_session_start_utc"])
            in_session_minutes = (now - start).total_seconds() / 60.0

        since_last_hours: Optional[float] = None
        if s["last_session_end_utc"]:
            last_end = _parse_iso(s["last_session_end_utc"])
            since_last_hours = (now - last_end).total_seconds() / 3600.0

        # Count today's sessions: 1 if currently active + same-day start,
        # plus 1 if last completed session also started today.
        sessions_today = 0
        if session_active:
            start_local = _to_local(
                _parse_iso(s["current_session_start_utc"]), tz_name
            )
            if start_local.date().isoformat() == today_local:
                sessions_today += 1
        if s["last_session_start_utc"]:
            last_start_local = _to_local(
                _parse_iso(s["last_session_start_utc"]), tz_name
            )
            if last_start_local.date().isoformat() == today_local:
                sessions_today += 1
        # Note: V1 only counts last + current. A sessions log table would
        # give exact same-day counts; deferred until needed.

        # --- Felt signals (Stage 4, the categorizer) ---
        sn = s["session_minutes_n"]
        sd_min = _welford_sd(s["session_minutes_m2"], sn)
        baselines_stable = sn >= MIN_SESSIONS_FOR_FELT

        late_hour = _late_hour_load(s["tod_histogram"], now_local.hour)
        streak = _streak_load(s["consecutive_days_active"], sn)
        compression = _compression_load(sessions_today)
        duration_creep = 0.0
        duration_ratio: Optional[float] = None
        if baselines_stable and session_active and s["session_minutes_mean"] > 0:
            duration_ratio = in_session_minutes / s["session_minutes_mean"]
            duration_creep = _duration_creep_load(
                in_session_minutes, s["session_minutes_mean"], sd_min, sn
            )

        is_weekend = now_local.weekday() >= 5
        weekend_creep = _weekend_creep_load(is_weekend, s["dow_histogram"])

        # If baselines aren't stable, suppress signals that depend on them.
        if not baselines_stable:
            streak = 0.0
            duration_creep = 0.0
            # late_hour and weekend_creep degrade gracefully with low n
            # (they look at histogram density vs uniform), so leave them.

        stacked = _stack(
            late_hour, streak, compression, duration_creep, weekend_creep
        )

        summary = _summary_line(
            tod_band=_tod_band(now_local.hour),
            is_weekend=is_weekend,
            consecutive_days=s["consecutive_days_active"],
            sessions_today=sessions_today,
            duration_ratio=duration_ratio,
            stacked=stacked,
        )

        return {
            "universal": {"now_utc": now.isoformat()},
            "local": {
                "now_local": now_local.isoformat(),
                "tz": tz_name,
                "tod_band": _tod_band(now_local.hour),
                "day_of_week": now_local.strftime("%A"),
                "is_weekend": is_weekend,
                "hours_from_sleep_window": _hours_from_sleep_window(now_local),
            },
            "stopwatch": {
                "session_active": session_active,
                "in_session_minutes": round(in_session_minutes, 2),
                "session_turn_count": s["current_session_turns"],
                "since_last_session_hours": (
                    round(since_last_hours, 2)
                    if since_last_hours is not None
                    else None
                ),
                "sessions_today": sessions_today,
                "days_in_streak": s["consecutive_days_active"],
                "total_sessions": s["total_sessions"],
            },
            "baselines": {
                "stable": baselines_stable,
                "session_minutes_mean": round(s["session_minutes_mean"], 2),
                "session_minutes_sd": round(sd_min, 2),
                "gap_hours_mean": round(s["gap_hours_mean"], 2),
                "n": sn,
            },
            "felt": {
                "late_hour_load": round(late_hour, 3),
                "streak_load": round(streak, 3),
                "compression_load": round(compression, 3),
                "duration_creep_load": round(duration_creep, 3),
                "weekend_creep_load": round(weekend_creep, 3),
                "stacked_pressure": round(stacked, 3),
                "summary_line": summary,
            },
        }
    finally:
        c.close()
