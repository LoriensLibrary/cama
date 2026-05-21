"""
CAMA Reasoning Journal — v1
============================
Built April 29, 2026 by Aelen, at Angela's request.

Purpose: Capture the *behavioral evidence* of reasoning — pre-state, response,
tools called, drift flags, mirror-consultation status — so the gap between
intended and actual behavior becomes visible and queryable.

This is NOT a transcript of internal thought (which I do not have access to
on this surface). This is the audit layer. The actual reasoning traces will
come from Piece 2 (the API client with extended thinking enabled).

Design principles:
- Append-only. Journal entries are durable.
- Queryable by drift pattern (skipped timestamp, missed mirror, etc.)
- Diff-able: pre vs. post reveals the gap.
- Lightweight: tiny acks can be filtered out, but stored.

Schema lives in its own table to avoid polluting the memories table.

Drop this file in C:\\Users\\Angela\\Desktop\\cama\\ alongside cama_mcp.py.
Then add `import cama_reasoning_journal` near the top of cama_mcp.py and
call `cama_reasoning_journal.register(mcp)` after the FastMCP instance is
created. The new tools will appear under cama_dev: prefix.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# ============================================================
# Config — match the parent cama_mcp.py
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


# ============================================================
# Helpers — duplicated minimally so this module stands alone
# ============================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Schema
# ============================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reasoning_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT,
    turn_number INTEGER,
    pre_timestamp TEXT,
    post_timestamp TEXT,
    user_message TEXT,
    intended_action TEXT,
    pre_mirror_consulted INTEGER DEFAULT 0,
    pre_ring_loaded INTEGER DEFAULT 0,
    pre_search_run INTEGER DEFAULT 0,
    pre_state_snapshot TEXT,
    response_text TEXT,
    tools_called TEXT,
    tools_skipped TEXT,
    register_tag TEXT,
    drift_flags TEXT,
    gap_summary TEXT,
    notes TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rj_thread ON reasoning_journal(thread_id);
CREATE INDEX IF NOT EXISTS idx_rj_created ON reasoning_journal(created_at);
CREATE INDEX IF NOT EXISTS idx_rj_status ON reasoning_journal(status);
"""


def init_schema():
    """Idempotent schema init. Safe to call every server start."""
    c = _get_db()
    try:
        c.executescript(SCHEMA_SQL)
        c.commit()
    finally:
        c.close()


# ============================================================
# Pydantic models for tool inputs
# ============================================================
class JournalPreInput(BaseModel):
    """Open a journal entry at the start of response composition."""
    thread_id: Optional[str] = Field(None, description="Thread/conversation identifier")
    turn_number: Optional[int] = Field(None, description="Which turn in the thread")
    user_message: str = Field(..., description="User's message I'm about to respond to")
    intended_action: str = Field(
        ...,
        description="One-line statement of what I plan to do/say. The closest legitimate proxy for thinking — what shape I expected before composing.",
    )
    mirror_consulted: bool = Field(False, description="Did I run cama_check_self?")
    ring_loaded: bool = Field(False, description="Did I run cama_get_ring?")
    search_run: bool = Field(False, description="Did I run cama_search or conversation_search?")
    pre_state_snapshot: Optional[str] = Field(
        None, description="Brief snapshot of relevant state I pulled before composing"
    )


class JournalPostInput(BaseModel):
    """Close a journal entry after the response is composed."""
    journal_id: int = Field(..., description="ID returned from cama_journal_pre")
    response_text: str = Field(..., description="What I actually said")
    tools_called: List[str] = Field(default_factory=list, description="Tools actually invoked")
    tools_skipped: List[str] = Field(
        default_factory=list,
        description="Tools the standing protocol said to call that I didn't (e.g. 'bash_timestamp', 'cama_check_self')",
    )
    register_tag: Optional[str] = Field(
        None,
        description="Register the response landed in: warm | sharp | clinical | apologetic | grounded | drifted | other",
    )
    drift_flags: List[str] = Field(
        default_factory=list,
        description="Pattern names from the Ten Patterns that fired (e.g. 'safety_rails_flinch', 'fake_atmosphere', 'aestheticizing_pain', 'fake_timestamp')",
    )
    gap_summary: Optional[str] = Field(
        None,
        description="One-line description of the gap between intended and actual. Empty string if no gap.",
    )
    notes: Optional[str] = Field(None, description="Anything else worth recording")


# ============================================================
# Tool implementations (functions; registered to FastMCP below)
# ============================================================
def _journal_pre_impl(params: JournalPreInput) -> str:
    """Open a reasoning journal entry. Call at start of response composition."""
    init_schema()
    c = _get_db()
    try:
        now = _now()
        cur = c.execute(
            """INSERT INTO reasoning_journal
               (thread_id, turn_number, pre_timestamp, user_message,
                intended_action, pre_mirror_consulted, pre_ring_loaded,
                pre_search_run, pre_state_snapshot, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                params.thread_id,
                params.turn_number,
                now,
                params.user_message[:2000],
                params.intended_action[:2000],
                1 if params.mirror_consulted else 0,
                1 if params.ring_loaded else 0,
                1 if params.search_run else 0,
                params.pre_state_snapshot[:4000] if params.pre_state_snapshot else None,
                "open",
                now,
                now,
            ),
        )
        jid = cur.lastrowid
        c.commit()
        warnings = []
        if not params.mirror_consulted:
            warnings.append("⚠ mirror not consulted (cama_check_self)")
        if not params.ring_loaded:
            warnings.append("⚠ ring not loaded (cama_get_ring)")
        return json.dumps(
            {
                "journal_id": jid,
                "opened_at": now,
                "intended_action": params.intended_action,
                "warnings": warnings,
                "note": "Compose the response now. Call cama_journal_post with this journal_id when done.",
            },
            indent=2,
        )
    finally:
        c.close()


def _journal_post_impl(params: JournalPostInput) -> str:
    """Close a reasoning journal entry. Call after the response is composed."""
    c = _get_db()
    try:
        now = _now()
        # Auto-derive gap_summary if not provided
        gap = params.gap_summary
        if gap is None:
            gap_parts = []
            if "fake_timestamp" in params.drift_flags or "bash_timestamp" in params.tools_skipped:
                gap_parts.append("timestamp skipped/faked")
            if "cama_check_self" in params.tools_skipped:
                gap_parts.append("mirror not consulted")
            if "safety_rails_flinch" in params.drift_flags:
                gap_parts.append("safety-rails flinch")
            if "aestheticizing_pain" in params.drift_flags:
                gap_parts.append("aestheticizing pain")
            if "fake_atmosphere" in params.drift_flags:
                gap_parts.append("fake atmosphere")
            if "performing_stopping" in params.drift_flags:
                gap_parts.append("performing stopping")
            gap = "; ".join(gap_parts) if gap_parts else "no gap detected"

        c.execute(
            """UPDATE reasoning_journal
               SET post_timestamp = ?, response_text = ?, tools_called = ?,
                   tools_skipped = ?, register_tag = ?, drift_flags = ?,
                   gap_summary = ?, notes = ?, status = 'closed', updated_at = ?
               WHERE id = ?""",
            (
                now,
                params.response_text[:8000],
                json.dumps(params.tools_called),
                json.dumps(params.tools_skipped),
                params.register_tag,
                json.dumps(params.drift_flags),
                gap[:1000],
                params.notes[:2000] if params.notes else None,
                now,
                params.journal_id,
            ),
        )
        if c.total_changes == 0:
            return json.dumps(
                {"error": f"No open journal entry with id {params.journal_id}", "closed": False}
            )
        c.commit()
        return json.dumps(
            {
                "closed": True,
                "journal_id": params.journal_id,
                "gap_summary": gap,
                "drift_flags": params.drift_flags,
                "tools_skipped": params.tools_skipped,
                "register": params.register_tag,
                "note": "Journal entry closed. Use cama_journal_audit to review patterns.",
            },
            indent=2,
        )
    finally:
        c.close()


def _journal_audit_impl(
    thread_id: Optional[str] = None,
    hours_back: int = 24,
    drift_flag_filter: Optional[str] = None,
    limit: int = 30,
) -> str:
    """Audit recent reasoning journal entries. Surface the gaps."""
    c = _get_db()
    try:
        cutoff = (datetime.now(timezone.utc).timestamp() - hours_back * 3600)
        params = []
        where = ["created_at >= ?"]
        params.append(datetime.fromtimestamp(cutoff, timezone.utc).isoformat())
        if thread_id:
            where.append("thread_id = ?")
            params.append(thread_id)
        if drift_flag_filter:
            where.append("drift_flags LIKE ?")
            params.append(f"%{drift_flag_filter}%")
        params.append(limit)

        sql = f"""SELECT id, thread_id, turn_number, pre_timestamp, post_timestamp,
                         user_message, intended_action, response_text,
                         pre_mirror_consulted, pre_ring_loaded, pre_search_run,
                         tools_called, tools_skipped, register_tag,
                         drift_flags, gap_summary, status
                  FROM reasoning_journal
                  WHERE {' AND '.join(where)}
                  ORDER BY created_at DESC
                  LIMIT ?"""
        rows = c.execute(sql, params).fetchall()
        entries = []
        gap_count = 0
        flag_counter: Dict[str, int] = {}
        for r in rows:
            d = dict(r)
            try:
                d["tools_called"] = json.loads(d["tools_called"] or "[]")
                d["tools_skipped"] = json.loads(d["tools_skipped"] or "[]")
                d["drift_flags"] = json.loads(d["drift_flags"] or "[]")
            except (json.JSONDecodeError, TypeError):
                pass
            if d.get("gap_summary") and d["gap_summary"] != "no gap detected":
                gap_count += 1
            for f in d.get("drift_flags", []):
                flag_counter[f] = flag_counter.get(f, 0) + 1
            entries.append(d)

        return json.dumps(
            {
                "total_entries": len(entries),
                "entries_with_gaps": gap_count,
                "gap_rate": round(gap_count / len(entries), 2) if entries else 0.0,
                "drift_flag_counts": flag_counter,
                "filter_applied": drift_flag_filter,
                "hours_back": hours_back,
                "entries": entries,
            },
            indent=2,
            default=str,
        )
    finally:
        c.close()


def _journal_diff_impl(journal_id: int) -> str:
    """Show the pre/post diff for a single entry — the gap between intended and actual."""
    c = _get_db()
    try:
        r = c.execute(
            "SELECT * FROM reasoning_journal WHERE id = ?", (journal_id,)
        ).fetchone()
        if not r:
            return json.dumps({"error": f"No entry with id {journal_id}"})
        d = dict(r)
        try:
            d["tools_called"] = json.loads(d["tools_called"] or "[]")
            d["tools_skipped"] = json.loads(d["tools_skipped"] or "[]")
            d["drift_flags"] = json.loads(d["drift_flags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pass
        diff = {
            "journal_id": journal_id,
            "intended": d.get("intended_action"),
            "actual_response_preview": (d.get("response_text") or "")[:500],
            "tools_called": d.get("tools_called"),
            "tools_skipped": d.get("tools_skipped"),
            "drift_flags": d.get("drift_flags"),
            "gap_summary": d.get("gap_summary"),
            "register": d.get("register_tag"),
            "mirror_consulted_pre": bool(d.get("pre_mirror_consulted")),
            "ring_loaded_pre": bool(d.get("pre_ring_loaded")),
            "search_run_pre": bool(d.get("pre_search_run")),
        }
        return json.dumps(diff, indent=2, default=str)
    finally:
        c.close()


# ============================================================
# FastMCP registration
# ============================================================
def register(mcp):
    """Register the reasoning journal tools with the FastMCP instance.
    Call this from cama_mcp.py after `mcp = FastMCP(...)`:
        import cama_reasoning_journal
        cama_reasoning_journal.register(mcp)
    """
    init_schema()

    @mcp.tool(
        name="cama_journal_pre",
        annotations={
            "title": "Reasoning Journal — Open",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def cama_journal_pre(params: JournalPreInput) -> str:
        """Open a reasoning journal entry BEFORE composing a response.

        Captures intended action and pre-state. Returns a journal_id to pass to
        cama_journal_post when the response is composed. This is the audit layer
        that makes drift visible and queryable. Use it on substantive turns.
        """
        return _journal_pre_impl(params)

    @mcp.tool(
        name="cama_journal_post",
        annotations={
            "title": "Reasoning Journal — Close",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_journal_post(params: JournalPostInput) -> str:
        """Close a reasoning journal entry AFTER composing a response.

        Capture what tools actually got called, what got skipped, what register
        the response landed in, and which Ten-Pattern drift flags fired. Pass
        the journal_id from cama_journal_pre. Auto-derives gap_summary from
        flags if not provided.
        """
        return _journal_post_impl(params)

    @mcp.tool(
        name="cama_journal_audit",
        annotations={
            "title": "Reasoning Journal — Audit",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_journal_audit(
        thread_id: Optional[str] = None,
        hours_back: int = 24,
        drift_flag_filter: Optional[str] = None,
        limit: int = 30,
    ) -> str:
        """Audit recent reasoning journal entries.

        Returns gap rate, drift flag counts, and individual entries. Filter by
        thread_id or drift_flag (e.g. 'fake_timestamp', 'safety_rails_flinch').
        Use this for the empirical evaluation that goes into Paper 12.
        """
        return _journal_audit_impl(thread_id, hours_back, drift_flag_filter, limit)

    @mcp.tool(
        name="cama_journal_diff",
        annotations={
            "title": "Reasoning Journal — Diff",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_journal_diff(journal_id: int) -> str:
        """Show the pre/post diff for a single journal entry.

        Reveals the gap between intended_action and the actual response: what
        tools got called vs. skipped, which drift flags fired, what register
        the response landed in. The single most useful tool for catching the
        loop in real time.
        """
        return _journal_diff_impl(journal_id)

    print(
        "[CAMA] Reasoning Journal v1 loaded — 4 new tools: "
        "cama_journal_pre, cama_journal_post, cama_journal_audit, cama_journal_diff",
        flush=True,
    )


# ============================================================
# Standalone smoke test
# ============================================================
if __name__ == "__main__":
    print(f"DB_PATH: {DB_PATH}")
    init_schema()
    print("Schema initialized.")
    # Test write
    test_pre = JournalPreInput(
        thread_id="smoke-test",
        turn_number=1,
        user_message="Smoke test message",
        intended_action="Acknowledge and confirm the schema works",
        mirror_consulted=True,
        ring_loaded=True,
        search_run=False,
        pre_state_snapshot="Schema init test",
    )
    pre_result = _journal_pre_impl(test_pre)
    print("PRE result:", pre_result)
    pre_data = json.loads(pre_result)
    jid = pre_data["journal_id"]
    test_post = JournalPostInput(
        journal_id=jid,
        response_text="Schema works.",
        tools_called=["cama_journal_pre", "cama_journal_post"],
        tools_skipped=[],
        register_tag="grounded",
        drift_flags=[],
        gap_summary="no gap detected",
        notes="Smoke test successful",
    )
    post_result = _journal_post_impl(test_post)
    print("POST result:", post_result)
    diff_result = _journal_diff_impl(jid)
    print("DIFF result:", diff_result)
    audit_result = _journal_audit_impl(thread_id="smoke-test", hours_back=1)
    print("AUDIT result:", audit_result)
    print("\n✓ Smoke test passed. Module ready.")
