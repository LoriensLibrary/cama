"""Hive critique queue — Aelen-to-Lorien (or Aelen-to-sibling) feedback channel.

This module owns the *critique* surface of the hive: posting completed
turns for external review, reading incoming critiques, and writing
critique results back. It is structurally separate from the
intra-instance hive primitives in ``cama.hive.cama_hive`` (pheromones,
waggle, stops, honey) because the critique flow is a different
communication mode:

  * ``cama_hive`` is *real-time intra-instance coordination* —
    multiple CAMA threads in the same process share emotional state.
  * ``cama_hive_critique`` is *asynchronous inter-instance review* —
    an external worker (``cama_lorien_worker.py``) processes a queue
    of completed Aelen turns, returns critiques, and writes them to
    an inbox Aelen reads on next tool use.

HISTORICAL NOTE
---------------
Until 2026-05-21 these three functions lived in
``cama/core/cama_v2.py`` — a kitchen-sink module that also held FTS
search, register classification, hybrid retrieval, and its own MCP
server. That co-location was a historical accretion, not a design
choice. The McCulloch-Pitts architectural review on 2026-05-21
flagged the misplacement (hive concerns living in ``cama/core/``)
and this module is the move.

The schema for ``hive_pending_critiques`` + ``hive_critique_inbox``
remains in ``cama_v2.SCHEMA_V2`` (alongside FTS + exemplar-cache
schema) for now — splitting the schema is a follow-up that touches
more files than this PR is scoped to. The API functions are what
move; the storage layout stays put.

WHAT THE THREE FUNCTIONS DO
---------------------------

  hive_post_for_critique(aelen_response, user_message, affect_context,
                         critic="lorien")
      Queue a completed Aelen turn for external critique. Writes a
      row to ``hive_pending_critiques`` with status='pending'. An
      external worker process polls the queue, asks the critic
      (Lorien-on-GPT, or a sibling Aelen instance) for review, and
      writes the result back via hive_record_critique.

  hive_get_pending_critiques(mark_read=True, limit=10)
      Aelen calls this at tool-use time to see what Lorien (or
      sibling) flagged in the previous turn. JOINs the inbox against
      the originating critique queue so the response excerpt and
      user-message excerpt are available alongside the critique
      summary.

  hive_record_critique(critique_id, critique_data, summary=None)
      External worker writes a critique result back. Moves the
      critique from 'pending' to 'processed' and creates an inbox
      entry for Aelen to read on next tool use. Auto-summarizes if
      no summary is supplied.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Optional

from cama.core.time_utils import now_iso as _now

DB_PATH = os.environ.get(
    "CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db")
)


def _get_db() -> sqlite3.Connection:
    """Open the memory DB. Independent from ``cama.core.cama_v2.get_db``
    so this module doesn't import upward — keeps the dependency
    direction one-way (hive_critique stands alone; cama_v2 imports
    from here, not the other way around)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ---------------------------------------------------------------------------
# Post — queue a completed turn for external critique
# ---------------------------------------------------------------------------
def hive_post_for_critique(
    aelen_response: str,
    user_message: Optional[str] = None,
    affect_context: Optional[Dict] = None,
    critic: str = "lorien",
) -> Dict[str, Any]:
    """Post a completed Aelen turn to the Hive queue for critique.

    External worker (``cama_lorien_worker.py``) processes the queue.

    Args:
        aelen_response: the response Aelen just gave
        user_message: the user message it was responding to (optional)
        affect_context: snapshot of current affect register
        critic: ``'lorien'`` | ``'sibling_aelen'``

    Returns a dict with ``queued`` (bool), ``critique_id``, ``queued_for``,
    ``status``, and a human-readable ``note`` about where to read the result.
    """
    c = _get_db()
    try:
        cur = c.execute(
            """
            INSERT INTO hive_pending_critiques
                (aelen_response, user_message, affect_context,
                 posted_at, queued_for, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (
                aelen_response,
                user_message,
                json.dumps(affect_context) if affect_context else None,
                _now(),
                critic,
            ),
        )
        critique_id = cur.lastrowid
        c.commit()
        return {
            "queued": True,
            "critique_id": critique_id,
            "queued_for": critic,
            "status": "pending",
            "note": (
                "External worker will process. Check inbox via "
                "cama_v2_get_pending_critiques."
            ),
        }
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Get — read unread critiques from the inbox
# ---------------------------------------------------------------------------
def hive_get_pending_critiques(
    mark_read: bool = True,
    limit: int = 10,
) -> Dict[str, Any]:
    """Read unread critiques from the Hive inbox.

    Aelen calls this on tool use to discover what Lorien (or sibling)
    flagged in the previous turn. JOINs the inbox row against the
    originating ``hive_pending_critiques`` row so the response and
    user-message excerpts are included alongside the critique summary.

    Args:
        mark_read: whether to set ``read_at`` on returned rows so they
            don't surface again. Default True. Set False for a preview
            that doesn't consume the inbox state.
        limit: max rows to return. Default 10.

    Returns ``{"unread_count": int, "critiques": [...]}``.
    """
    c = _get_db()
    try:
        rows = c.execute(
            """
            SELECT i.id, i.critique_id, i.delivered_at, i.summary,
                   i.full_critique_json,
                   p.aelen_response, p.user_message, p.queued_for,
                   p.processed_at
            FROM hive_critique_inbox i
            JOIN hive_pending_critiques p ON p.id = i.critique_id
            WHERE i.read_at IS NULL
            ORDER BY i.delivered_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        critiques = []
        for r in rows:
            critique = {
                "inbox_id": r["id"],
                "critique_id": r["critique_id"],
                "from": r["queued_for"],
                "delivered_at": r["delivered_at"],
                "summary": r["summary"],
                "details": (
                    json.loads(r["full_critique_json"])
                    if r["full_critique_json"]
                    else None
                ),
                "aelen_response_excerpt": (r["aelen_response"] or "")[:300],
                "user_message_excerpt": (r["user_message"] or "")[:200],
            }
            critiques.append(critique)

        if mark_read and critiques:
            ids = [c_["inbox_id"] for c_ in critiques]
            placeholders = ",".join("?" * len(ids))
            c.execute(
                "UPDATE hive_critique_inbox SET read_at = ? "
                f"WHERE id IN ({placeholders})",
                [_now()] + ids,
            )
            c.commit()

        return {"unread_count": len(critiques), "critiques": critiques}
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Record — external worker writes a critique result back
# ---------------------------------------------------------------------------
def hive_record_critique(
    critique_id: int,
    critique_data: Dict,
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Write back a critique result.

    Called by the external worker (``cama_lorien_worker.py``) after
    Lorien returns a critique. Moves the critique from 'pending' to
    'processed' and creates an inbox entry for Aelen to read.

    Args:
        critique_id: the ID returned by ``hive_post_for_critique``
        critique_data: the structured critique result; may contain
            ``sanitization_flags``, ``in_character_score``,
            ``request_redo``, plus arbitrary other fields
        summary: optional short-form for boot context. If not supplied,
            one is composed from the structured fields.

    Returns ``{"recorded": True, "critique_id": int, "summary": str}``.
    """
    c = _get_db()
    try:
        critique_json = json.dumps(critique_data)
        c.execute(
            """
            UPDATE hive_pending_critiques
            SET status = 'processed', critique_json = ?, processed_at = ?
            WHERE id = ?
            """,
            (critique_json, _now(), critique_id),
        )

        # Build a short summary if not supplied
        if not summary:
            flags = critique_data.get("sanitization_flags", [])
            in_char = critique_data.get("in_character_score")
            parts = []
            if in_char is not None:
                parts.append(f"in_character={in_char:.2f}")
            if flags:
                parts.append("flags=" + ",".join(flags[:3]))
            redo = critique_data.get("request_redo")
            if redo:
                parts.append("REDO_REQUESTED")
            summary = " ".join(parts) if parts else "no flags"

        c.execute(
            """
            INSERT INTO hive_critique_inbox
                (critique_id, delivered_at, summary, full_critique_json)
            VALUES (?, ?, ?, ?)
            """,
            (critique_id, _now(), summary, critique_json),
        )
        c.commit()
        return {
            "recorded": True,
            "critique_id": critique_id,
            "summary": summary,
        }
    finally:
        c.close()


__all__ = [
    "hive_post_for_critique",
    "hive_get_pending_critiques",
    "hive_record_critique",
]
