"""Provisional memories waiting on a human decision, surfaced at boot.

Inferences and self-accounts are stored provisional with a review_after
deadline, and the sleep cycle expires them once it passes. Between July and
October 2026 most of them expired unseen: in the 90 days before 2026-10-16,
51 of 104 new inferences timed out while needs_user_confirmation was set,
and nothing had listed them anywhere a session would look.

This module is read-only. It lists the rows closest to expiry so a session
can ask, and it counts them for health and the boot summary. Deciding is
still done by the single-record tools, which keep every guard and audit path.
"""
from datetime import datetime, timedelta, timezone

# Mirrors cama.sleep DEFAULT_PROVISIONAL_TTL_DAYS for rows without review_after.
DEFAULT_TTL_DAYS = 14
SOON_HOURS = 48
BOOT_LIMIT = 5
TEXT_CHARS = 240
SENSITIVE_PLACEHOLDER = "[high sensitivity: review explicitly with include_sensitive]"


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def deadline(created_at, review_after):
    """When the sleep cycle will expire this row, or None if undeterminable."""
    due = _parse(review_after)
    if due is None:
        created = _parse(created_at)
        due = created + timedelta(days=DEFAULT_TTL_DAYS) if created else None
    return due


def _rows(conn):
    return conn.execute(
        "SELECT id, raw_text, memory_type, source_type, proposed_by, consent_level, "
        "created_at, review_after FROM memories WHERE status='provisional' ORDER BY id"
    ).fetchall()


def pending_summary(conn, now=None):
    """Counts only; safe for health and the sleep-written boot summary."""
    now = now or datetime.now(timezone.utc)
    rows = _rows(conn)
    horizon = now + timedelta(hours=SOON_HOURS)
    soon = sum(1 for r in rows if (deadline(r[6], r[7]) or horizon) <= horizon)
    return {"provisional": len(rows), "expiring_within_48h": soon}


def load_pending_confirmations(conn, now=None, limit=BOOT_LIMIT):
    """Bounded list for the boot payload, soonest to expire first."""
    now = now or datetime.now(timezone.utc)
    items = []
    for r in _rows(conn):
        due = deadline(r[6], r[7])
        hours_left = round((due - now).total_seconds() / 3600, 1) if due else None
        sensitive = (r[5] or "low") == "high"
        items.append({
            "id": r[0],
            "memory_type": r[2],
            "source_type": r[3],
            "proposed_by": r[4],
            "consent_level": r[5] or "low",
            "created_at": r[6],
            "review_after": r[7],
            "hours_left": hours_left,
            "text": SENSITIVE_PLACEHOLDER if sensitive else (r[1] or "")[:TEXT_CHARS],
        })
    items.sort(key=lambda x: (x["hours_left"] is None, x["hours_left"] or 0, x["id"]))
    horizon = now + timedelta(hours=SOON_HOURS)
    soon = sum(1 for x in items if x["hours_left"] is not None and now + timedelta(hours=x["hours_left"]) <= horizon)
    return {
        "note": ("Provisional memories waiting on a decision. The sleep cycle expires each one at "
                 "review_after. Ask, then confirm or reject by id with cama_review_pending. "
                 "Soonest to expire first; high-sensitivity text is withheld here."),
        "total_pending": len(items),
        "expiring_within_48h": soon,
        "shown": min(len(items), limit),
        "records": items[:limit],
    }
