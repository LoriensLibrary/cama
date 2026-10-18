"""Retrieval feedback: what was shown, what was used.

The table existed in the live store since March 2026 with one synthetic row and
no writer anywhere in the tree (review finding K-3). Nothing recorded whether
the memories a query returned were ever used, so retrieval could only be tuned
by hand. This module is the writer. It changes no ranking; it gives ranking
something to learn from later.

Rows are small: memory id, a bounded copy of the query text, the feedback kind
('shown' at query time, 'used' when the assistant says a memory shaped its
reply, 'unhelpful' when it did not), and a weight delta reserved for a later
learning pass (always 0.0 here). Stdlib only; never raises into retrieval.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from cama.core.time_utils import now_iso

FEEDBACK_KINDS = ("shown", "used", "unhelpful")
QUERY_CHARS = 500
MAX_IDS = 50

_DDL = """
CREATE TABLE IF NOT EXISTS retrieval_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    query_text TEXT,
    feedback TEXT NOT NULL,
    weight_delta REAL DEFAULT 0.0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rf_memory ON retrieval_feedback(memory_id, created_at);
CREATE INDEX IF NOT EXISTS idx_rf_kind ON retrieval_feedback(feedback, created_at);
"""


def ensure_table(c: sqlite3.Connection) -> None:
    c.executescript(_DDL)


def _clean_ids(memory_ids: Iterable[int]) -> list[int]:
    out: list[int] = []
    for mid in memory_ids:
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        if mid > 0 and mid not in out:
            out.append(mid)
        if len(out) >= MAX_IDS:
            break
    return out


def record(c: sqlite3.Connection, memory_ids: Iterable[int], feedback: str, query_text: str | None = None) -> int:
    """Insert one row per id. Returns the number written. Does not commit."""
    if feedback not in FEEDBACK_KINDS:
        raise ValueError(f"feedback must be one of {FEEDBACK_KINDS}")
    ids = _clean_ids(memory_ids)
    if not ids:
        return 0
    ensure_table(c)
    q = (query_text or "")[:QUERY_CHARS] or None
    now = now_iso()
    c.executemany(
        "INSERT INTO retrieval_feedback (memory_id, query_text, feedback, weight_delta, created_at) VALUES (?,?,?,0.0,?)",
        [(mid, q, feedback, now) for mid in ids],
    )
    return len(ids)


def record_shown(c: sqlite3.Connection, query_text: str | None, memory_ids: Iterable[int]) -> int:
    return record(c, memory_ids, "shown", query_text)


def summary(c: sqlite3.Connection, days: int = 7) -> dict:
    """Counts by kind over a window, plus how many shown memories were later used."""
    ensure_table(c)
    rows = c.execute(
        "SELECT feedback, COUNT(*) FROM retrieval_feedback "
        "WHERE created_at >= datetime('now', ?) AND substr(COALESCE(query_text,''),1,2) <> '__' "
        "GROUP BY feedback",
        (f"-{int(days)} days",),
    ).fetchall()
    counts = {k: 0 for k in FEEDBACK_KINDS}
    for kind, n in rows:
        if kind in counts:
            counts[kind] = n
    shown_ids = c.execute(
        "SELECT COUNT(DISTINCT memory_id) FROM retrieval_feedback WHERE feedback='shown' "
        "AND created_at >= datetime('now', ?)", (f"-{int(days)} days",)).fetchone()[0]
    used_ids = c.execute(
        "SELECT COUNT(DISTINCT s.memory_id) FROM retrieval_feedback s "
        "WHERE s.feedback='shown' AND s.created_at >= datetime('now', ?) AND EXISTS ("
        "SELECT 1 FROM retrieval_feedback u WHERE u.memory_id=s.memory_id AND u.feedback='used' "
        "AND u.created_at >= s.created_at)", (f"-{int(days)} days",)).fetchone()[0]
    return {"window_days": int(days), **counts,
            "distinct_shown": shown_ids, "distinct_shown_then_used": used_ids,
            "note": "shown is written by cama_query_memories; used and unhelpful by cama_mark_used. "
                    "Nothing reads these for ranking yet."}
