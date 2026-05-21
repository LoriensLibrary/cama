"""
cama_inject.py — Standalone add-on for human interject during agentic work.

Two pieces:
  1. Tool functions that get registered in cama_mcp.py (cama_post_note, cama_get_pending_notes)
  2. CLI: `python cama_inject.py "your message"` writes a note Aelen will see

USAGE (CLI):
    python cama_inject.py "stop, that's the wrong path"
    python cama_inject.py --priority high "abort the import"
    python cama_inject.py --list   # show your unread notes
    python cama_inject.py --clear  # mark all as read

INTEGRATION INTO cama_mcp.py:
    1. Import this module:    from cama_inject import init_pending_notes_table, post_note, get_pending_notes
    2. Call init at startup:   init_pending_notes_table()
    3. Add two MCP tools (see register_inject_tools() at bottom for templates)
    4. Optionally: have cama_thread_start and other read tools auto-check pending notes

The point: you type one line from any terminal; Aelen sees it within seconds.
"""

import argparse
import os
import sqlite3
import sys

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


from cama.core.time_utils import now_iso as _now


def init_pending_notes_table(db_path=None):
    """Create pending_notes table if missing. Idempotent."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            priority TEXT DEFAULT 'normal',
            created_at TEXT NOT NULL,
            read_at TEXT,
            session_id TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_notes_unread
        ON pending_notes(read_at) WHERE read_at IS NULL
    """)
    conn.commit()
    conn.close()


def post_note(text, priority="normal", db_path=None):
    """Write a note to be seen by the next tool-call check."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO pending_notes (text, priority, created_at) VALUES (?, ?, ?)",
        (text, priority, _now())
    )
    note_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"note_id": note_id, "posted_at": _now()}


def get_pending_notes(mark_read=True, db_path=None):
    """Read all unread notes. Marks them read by default."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, text, priority, created_at FROM pending_notes "
        "WHERE read_at IS NULL ORDER BY id ASC"
    ).fetchall()
    notes = [dict(r) for r in rows]
    if mark_read and notes:
        ids = [n["id"] for n in notes]
        conn.execute(
            f"UPDATE pending_notes SET read_at = ? WHERE id IN ({','.join('?' * len(ids))})",
            [_now()] + ids
        )
        conn.commit()
    conn.close()
    return {"notes": notes, "count": len(notes)}


def list_all_notes(unread_only=False, db_path=None):
    """List notes (for CLI inspection)."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM pending_notes"
    if unread_only:
        q += " WHERE read_at IS NULL"
    q += " ORDER BY id DESC LIMIT 30"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_all_notes(db_path=None):
    """Mark all unread as read (CLI helper)."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "UPDATE pending_notes SET read_at = ? WHERE read_at IS NULL",
        (_now(),)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Post a note to Aelen mid-session, or inspect notes."
    )
    ap.add_argument("text", nargs="*", help="The note to post")
    ap.add_argument("--priority", choices=["low", "normal", "high"], default="normal")
    ap.add_argument("--list", action="store_true", help="List recent notes")
    ap.add_argument("--unread", action="store_true", help="(with --list) only unread")
    ap.add_argument("--clear", action="store_true", help="Mark all unread as read")
    ap.add_argument("--db", default=None, help="Override DB path")
    args = ap.parse_args()

    init_pending_notes_table(args.db)

    if args.list:
        notes = list_all_notes(unread_only=args.unread, db_path=args.db)
        if not notes:
            print("No notes.")
            return
        for n in notes:
            status = "[UNREAD]" if not n["read_at"] else "[read]"
            print(f"#{n['id']:4d} {status:8s} [{n['priority']:6s}] {n['created_at'][:19]}")
            print(f"        {n['text']}")
        return

    if args.clear:
        n = clear_all_notes(args.db)
        print(f"Marked {n} note(s) as read.")
        return

    if not args.text:
        print("Usage: python cama_inject.py \"your message\"", file=sys.stderr)
        print("       python cama_inject.py --list", file=sys.stderr)
        sys.exit(1)

    text = " ".join(args.text)
    result = post_note(text, args.priority, args.db)
    print(f"Posted note #{result['note_id']} at {result['posted_at']}")
    print(f"  {text}")


# ─── MCP REGISTRATION TEMPLATE ──────────────────────────────────────────────
# Copy these blocks into cama_mcp.py. They use your existing FastMCP `mcp` instance.
#
# Add at top of cama_mcp.py (after other imports):
#     from cama_inject import init_pending_notes_table, post_note, get_pending_notes
#
# Add somewhere during startup (e.g., near the FastMCP init around line 680):
#     init_pending_notes_table()
#
# Add these two tool registrations alongside other @mcp.tool blocks:
#
# @mcp.tool(name="cama_post_note", annotations={"title":"Post Note","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False})
# async def cama_post_note_tool(text: str, priority: str = "normal") -> str:
#     """Post a note for the AI to see during agentic work. Used by Angela via CLI."""
#     result = post_note(text, priority)
#     return json.dumps(result)
#
# @mcp.tool(name="cama_get_pending_notes", annotations={"title":"Get Pending Notes","readOnlyHint":True,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False})
# async def cama_get_pending_notes_tool(mark_read: bool = True) -> str:
#     """Check for any notes Angela posted mid-session. Call this between major tool calls during long work."""
#     result = get_pending_notes(mark_read=mark_read)
#     return json.dumps(result, indent=2)


if __name__ == "__main__":
    main()
