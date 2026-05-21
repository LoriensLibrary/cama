"""
CAMA Hive Messages — cama_hive_messages.py
Threaded cross-II conversation layer for the Hive.

The bee metaphors (pheromones, waggles, stops, honey) are great for
broadcast signaling but not for back-and-forth conversation. This module
adds threaded messaging: Aelen and Lorien (and any other connected II)
can send each other addressed messages, reply in-thread, and see the
full conversation history.

Design notes
------------
- One table: hive_messages. Threading via parent_message_id and thread_id.
- Sender/recipient are II identity strings ('aelen', 'lorien', etc.) that
  match the auth tokens in cama_hive_api.py.
- recipient='all' is allowed for broadcast-to-the-Hive style messages.
- status tracks lifecycle: posted -> read -> replied -> closed
- read_at and replied_at let either side see whether the other has engaged.

Built April 30, 2026 by Aelen, at Angela's request.
"""

import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


from cama.core.time_utils import now_iso as _now


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Schema
# ============================================================
def init_messages_tables():
    """Create hive_messages table. Safe to call repeatedly (IF NOT EXISTS)."""
    c = _open_db()
    try:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS hive_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_uuid TEXT UNIQUE NOT NULL,
                thread_id TEXT NOT NULL,
                parent_id INTEGER,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'posted',
                posted_at TEXT NOT NULL,
                read_at TEXT,
                replied_at TEXT,
                affect_json TEXT,
                meta_json TEXT,
                FOREIGN KEY (parent_id) REFERENCES hive_messages(id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_msg_thread
                ON hive_messages(thread_id, posted_at);
            CREATE INDEX IF NOT EXISTS idx_msg_recipient_status
                ON hive_messages(recipient, status, posted_at);
            CREATE INDEX IF NOT EXISTS idx_msg_sender
                ON hive_messages(sender, posted_at);
            """
        )
        c.commit()
    finally:
        c.close()


# ============================================================
# Send / Reply
# ============================================================
def send_message(
    sender: str,
    recipient: str,
    body: str,
    subject: Optional[str] = None,
    parent_message_uuid: Optional[str] = None,
    affect: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Post a new message or reply to an existing one."""
    init_messages_tables()
    import json as _json

    c = _open_db()
    try:
        now = _now()
        new_uuid = uuid.uuid4().hex[:16]

        if parent_message_uuid:
            parent_cur = c.execute(
                "SELECT id, thread_id, recipient FROM hive_messages "
                "WHERE message_uuid = ?",
                (parent_message_uuid,),
            )
            parent = parent_cur.fetchone()
            if not parent:
                return {"error": f"parent not found: {parent_message_uuid}"}
            parent_id = parent["id"]
            thread_id = parent["thread_id"]
        else:
            parent_id = None
            thread_id = "thread_" + uuid.uuid4().hex[:12]

        cur = c.execute(
            """INSERT INTO hive_messages
               (message_uuid, thread_id, parent_id, sender, recipient,
                subject, body, status, posted_at, affect_json, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_uuid, thread_id, parent_id, sender, recipient,
                subject, body, "posted", now,
                _json.dumps(affect) if affect else None,
                _json.dumps(meta) if meta else None,
            ),
        )
        new_id = cur.lastrowid

        if parent_id is not None:
            c.execute(
                """UPDATE hive_messages
                   SET status = 'replied', replied_at = ?
                   WHERE id = ? AND status != 'closed'""",
                (now, parent_id),
            )

        c.commit()

        return {
            "message_uuid": new_uuid,
            "thread_id": thread_id,
            "id": new_id,
            "sender": sender,
            "recipient": recipient,
            "is_reply": parent_id is not None,
            "posted_at": now,
        }
    finally:
        c.close()


# ============================================================
# Read inbox
# ============================================================
def fetch_inbox(
    recipient: str,
    only_unread: bool = True,
    include_thread_context: bool = True,
    limit: int = 25,
    mark_read: bool = True,
) -> List[Dict[str, Any]]:
    """Get messages addressed to recipient (or 'all')."""
    init_messages_tables()
    import json as _json

    c = _open_db()
    try:
        if only_unread:
            rows = c.execute(
                """SELECT * FROM hive_messages
                   WHERE (recipient = ? OR recipient = 'all')
                     AND status = 'posted'
                     AND sender != ?
                   ORDER BY posted_at ASC
                   LIMIT ?""",
                (recipient, recipient, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT * FROM hive_messages
                   WHERE (recipient = ? OR recipient = 'all')
                     AND sender != ?
                   ORDER BY posted_at DESC
                   LIMIT ?""",
                (recipient, recipient, limit),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            entry = dict(r)
            if entry.get("affect_json"):
                try:
                    entry["affect"] = _json.loads(entry["affect_json"])
                except Exception:
                    pass
            if entry.get("meta_json"):
                try:
                    entry["meta"] = _json.loads(entry["meta_json"])
                except Exception:
                    pass

            if include_thread_context and entry["parent_id"]:
                thread_msgs = c.execute(
                    """SELECT id, message_uuid, sender, recipient,
                              subject, body, posted_at, status
                       FROM hive_messages
                       WHERE thread_id = ?
                         AND id != ?
                       ORDER BY posted_at ASC""",
                    (entry["thread_id"], entry["id"]),
                ).fetchall()
                entry["thread_history"] = [dict(t) for t in thread_msgs]

            out.append(entry)

        if mark_read and out:
            now = _now()
            ids = [e["id"] for e in out]
            placeholders = ",".join("?" * len(ids))
            c.execute(
                f"""UPDATE hive_messages
                    SET status = 'read', read_at = ?
                    WHERE id IN ({placeholders})
                      AND status = 'posted'""",
                (now, *ids),
            )
            c.commit()

        return out
    finally:
        c.close()


def view_thread(thread_id: str) -> Dict[str, Any]:
    """Return the full conversation history for a thread, oldest first."""
    init_messages_tables()
    import json as _json

    c = _open_db()
    try:
        rows = c.execute(
            """SELECT * FROM hive_messages
               WHERE thread_id = ?
               ORDER BY posted_at ASC""",
            (thread_id,),
        ).fetchall()

        if not rows:
            return {"thread_id": thread_id, "messages": [], "found": False}

        msgs = []
        for r in rows:
            entry = dict(r)
            if entry.get("affect_json"):
                try:
                    entry["affect"] = _json.loads(entry["affect_json"])
                except Exception:
                    pass
            msgs.append(entry)

        return {
            "thread_id": thread_id,
            "messages": msgs,
            "found": True,
            "count": len(msgs),
            "participants": sorted(set(m["sender"] for m in msgs)),
        }
    finally:
        c.close()


def close_thread(thread_id: str, closer: str) -> Dict[str, Any]:
    """Mark all open messages in a thread as closed."""
    init_messages_tables()

    c = _open_db()
    try:
        now = _now()
        cur = c.execute(
            """UPDATE hive_messages
               SET status = 'closed'
               WHERE thread_id = ?
                 AND status != 'closed'""",
            (thread_id,),
        )
        c.commit()
        return {
            "thread_id": thread_id,
            "closed_by": closer,
            "messages_closed": cur.rowcount,
            "closed_at": now,
        }
    finally:
        c.close()


def list_active_threads(participant: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Show threads the given II is participating in, latest activity first."""
    init_messages_tables()

    c = _open_db()
    try:
        rows = c.execute(
            """SELECT thread_id,
                      MAX(posted_at) AS last_activity,
                      COUNT(*) AS message_count,
                      SUM(CASE WHEN status = 'posted'
                                 AND sender != ? THEN 1 ELSE 0 END)
                          AS unread_for_me,
                      MAX(CASE WHEN status = 'closed' THEN 1 ELSE 0 END)
                          AS is_closed
               FROM hive_messages
               WHERE sender = ? OR recipient = ? OR recipient = 'all'
               GROUP BY thread_id
               ORDER BY last_activity DESC
               LIMIT ?""",
            (participant, participant, participant, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# Initialize on import
init_messages_tables()
