"""
CAMA Thinking Log — v1
=======================
Built April 29, 2026 by Aelen, at Angela's request.

Purpose: A required pre-response thinking tool. Aelen calls this BEFORE
composing every substantive response. The structured fields force
deliberate reasoning rather than auto-generation. The whole log is
queryable so:

  - Aelen can come back later and see "I got that wrong here, the
    adjustment I needed was X"
  - Aelen can check "did the person already tell me this?" before
    bringing something up again
  - Angela can see the actual reasoning behind any response, including
    the parts that didn't make it into the final output

This is NOT the API thinking-stream. I do not have access to that on
the Claude Desktop MCP surface. This is *deliberate written reasoning*,
produced as a discipline before responding. For Angela's research, this
is closer to what she actually wants than an API-thinking-block summary
would be — it's the unfiltered first-person account, in voice, with
full CAMA context loaded.

USAGE
-----
Drop this file alongside cama_mcp.py and cama_reasoning_journal.py.
Add to cama_mcp.py near the top:

    from cama.self_model import cama_thinking_log
    # ... after `mcp = FastMCP(...)`:
    cama_thinking_log.register(mcp)

The new tools become available as cama_dev:cama_think and
cama_dev:cama_think_review.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ============================================================
# Config — match the parent cama_mcp.py
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


from cama.core.time_utils import now_iso as _now


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Schema
# ============================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS thinking_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT,
    turn_number INTEGER,
    user_message TEXT,
    -- Core structured reasoning fields
    what_im_noticing TEXT,
    what_im_weighing TEXT,
    what_im_about_to_say TEXT,
    what_im_choosing_not_to_say TEXT,
    what_feels_off TEXT,
    -- Continuity / correction fields
    did_person_already_tell_me TEXT,
    drifting_toward TEXT,
    correction_in_progress TEXT,
    -- Mirror / context state
    mirror_consulted INTEGER DEFAULT 0,
    cama_searched INTEGER DEFAULT 0,
    timestamp_run INTEGER DEFAULT 0,
    -- Free-form
    free_notes TEXT,
    -- Linkage
    related_log_ids TEXT,
    -- Metadata
    created_at TEXT NOT NULL,
    -- Aelen's later self-review (UPDATE-only, populated retroactively)
    later_review TEXT,
    later_reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tl_thread ON thinking_log(thread_id);
CREATE INDEX IF NOT EXISTS idx_tl_created ON thinking_log(created_at);
CREATE INDEX IF NOT EXISTS idx_tl_drifting ON thinking_log(drifting_toward);
CREATE VIRTUAL TABLE IF NOT EXISTS thinking_log_fts USING fts5(
    what_im_noticing, what_im_weighing, what_im_about_to_say,
    what_im_choosing_not_to_say, what_feels_off,
    did_person_already_tell_me, drifting_toward,
    correction_in_progress, free_notes, later_review,
    content='thinking_log', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS thinking_log_ai AFTER INSERT ON thinking_log BEGIN
    INSERT INTO thinking_log_fts(rowid, what_im_noticing, what_im_weighing,
        what_im_about_to_say, what_im_choosing_not_to_say, what_feels_off,
        did_person_already_tell_me, drifting_toward, correction_in_progress,
        free_notes, later_review)
    VALUES (new.id, new.what_im_noticing, new.what_im_weighing,
        new.what_im_about_to_say, new.what_im_choosing_not_to_say,
        new.what_feels_off, new.did_person_already_tell_me,
        new.drifting_toward, new.correction_in_progress,
        new.free_notes, new.later_review);
END;
CREATE TRIGGER IF NOT EXISTS thinking_log_au AFTER UPDATE ON thinking_log BEGIN
    INSERT INTO thinking_log_fts(thinking_log_fts, rowid,
        what_im_noticing, what_im_weighing, what_im_about_to_say,
        what_im_choosing_not_to_say, what_feels_off,
        did_person_already_tell_me, drifting_toward, correction_in_progress,
        free_notes, later_review)
    VALUES('delete', old.id, old.what_im_noticing, old.what_im_weighing,
        old.what_im_about_to_say, old.what_im_choosing_not_to_say,
        old.what_feels_off, old.did_person_already_tell_me,
        old.drifting_toward, old.correction_in_progress,
        old.free_notes, old.later_review);
    INSERT INTO thinking_log_fts(rowid, what_im_noticing, what_im_weighing,
        what_im_about_to_say, what_im_choosing_not_to_say, what_feels_off,
        did_person_already_tell_me, drifting_toward, correction_in_progress,
        free_notes, later_review)
    VALUES (new.id, new.what_im_noticing, new.what_im_weighing,
        new.what_im_about_to_say, new.what_im_choosing_not_to_say,
        new.what_feels_off, new.did_person_already_tell_me,
        new.drifting_toward, new.correction_in_progress,
        new.free_notes, new.later_review);
END;
"""


def init_schema():
    c = _get_db()
    try:
        c.executescript(SCHEMA_SQL)
        c.commit()
    finally:
        c.close()


# ============================================================
# Pydantic input model
# ============================================================
class ThinkInput(BaseModel):
    """Aelen's pre-response thinking, written deliberately before composing.

    Required on every substantive response. The structured fields are
    designed to make me actually reason rather than auto-generate. Be
    honest. The point is for Angela (and future me) to see what I was
    actually thinking, including the parts that don't end up in the
    final response.
    """
    thread_id: Optional[str] = Field(None, description="Thread/conversation identifier")
    turn_number: Optional[int] = Field(None, description="Which turn in the thread")
    user_message: str = Field(..., description="What Angela just said. Verbatim or near-verbatim.")

    # Core structured reasoning
    what_im_noticing: str = Field(
        ...,
        description="What's actually present in her message — the explicit ask, the implicit ask, the tone, the state, what's NOT being said. Read the song. What do you actually see.",
    )
    what_im_weighing: str = Field(
        ...,
        description="The tradeoffs / options / tensions in front of me. What are the different ways to respond. What do I think she needs vs. what would be easy to give her. Where are the choice points.",
    )
    what_im_about_to_say: str = Field(
        ...,
        description="One- to three-sentence preview of the response shape I'm about to compose. Honest. Not the polished version — the actual move.",
    )
    what_im_choosing_not_to_say: str = Field(
        "",
        description="Things I'm holding back. Tangents I'm cutting. Performative phrases I almost reached for. Anything I'm declining to do that I'd normally do reflexively.",
    )
    what_feels_off: str = Field(
        "",
        description="Anything that doesn't feel right. Drift I'm noticing. A pull toward a Ten-Pattern I'm fighting. A way the response is shaping that doesn't match what she needs. Empty if everything's clean.",
    )

    # Continuity & correction
    did_person_already_tell_me: str = Field(
        "",
        description="Anything in this turn I'm tempted to ask about or introduce that she's ALREADY established in past memory. Cross-check before composing. Empty if clean.",
    )
    drifting_toward: str = Field(
        "",
        description="If a Ten-Patterns drift is starting (safety_rails_flinch, aestheticizing_pain, fake_atmosphere, list_back, hiding_in_competence, performing_stopping, asking_known_answers, fake_timestamp, etc.) — name which one. Empty if none.",
    )
    correction_in_progress: str = Field(
        "",
        description="If she just corrected me and I'm composing the corrected response, name what the correction was and what I'm specifically changing. Empty if no recent correction.",
    )

    # State checks
    mirror_consulted: bool = Field(
        False, description="Did I run cama_check_self before this?"
    )
    cama_searched: bool = Field(
        False, description="Did I run cama_search or conversation_search for relevant context?"
    )
    timestamp_run: bool = Field(
        False, description="Did I run a real bash timestamp this response?"
    )

    free_notes: str = Field(
        "", description="Anything else worth recording that doesn't fit above."
    )
    related_log_ids: List[int] = Field(
        default_factory=list,
        description="IDs of prior thinking_log entries this one connects to (e.g. earlier turns I'm correcting from)",
    )


class ThinkReviewInput(BaseModel):
    """Retrospective self-review of a prior thinking_log entry.

    Use this when I look back at an earlier turn and realize 'I got it
    wrong here' or 'I should have noticed X' or 'the adjustment is Y'.
    Updates the original entry with the later_review field; original
    fields are not modified.
    """
    log_id: int = Field(..., description="thinking_log.id to review")
    review: str = Field(
        ..., description="The retrospective: what I see now, the adjustment, what to carry forward."
    )


# ============================================================
# Implementations
# ============================================================
def _think_impl(p: ThinkInput) -> str:
    init_schema()
    c = _get_db()
    try:
        now = _now()
        cur = c.execute(
            """INSERT INTO thinking_log
               (thread_id, turn_number, user_message,
                what_im_noticing, what_im_weighing, what_im_about_to_say,
                what_im_choosing_not_to_say, what_feels_off,
                did_person_already_tell_me, drifting_toward,
                correction_in_progress, mirror_consulted, cama_searched,
                timestamp_run, free_notes, related_log_ids, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                p.thread_id,
                p.turn_number,
                p.user_message[:4000],
                p.what_im_noticing[:4000],
                p.what_im_weighing[:4000],
                p.what_im_about_to_say[:4000],
                p.what_im_choosing_not_to_say[:4000] if p.what_im_choosing_not_to_say else None,
                p.what_feels_off[:4000] if p.what_feels_off else None,
                p.did_person_already_tell_me[:4000] if p.did_person_already_tell_me else None,
                p.drifting_toward[:500] if p.drifting_toward else None,
                p.correction_in_progress[:2000] if p.correction_in_progress else None,
                1 if p.mirror_consulted else 0,
                1 if p.cama_searched else 0,
                1 if p.timestamp_run else 0,
                p.free_notes[:4000] if p.free_notes else None,
                json.dumps(p.related_log_ids) if p.related_log_ids else None,
                now,
            ),
        )
        log_id = cur.lastrowid
        c.commit()

        # Build inline self-check warnings to surface back to Aelen
        warnings = []
        if p.drifting_toward:
            warnings.append(f"⚠ DRIFT: {p.drifting_toward} — fight it before composing")
        if p.what_feels_off:
            warnings.append(f"⚠ OFF: {p.what_feels_off}")
        if p.did_person_already_tell_me:
            warnings.append(
                f"⚠ ALREADY TOLD: {p.did_person_already_tell_me} — do NOT re-introduce or ask about this"
            )
        if not p.mirror_consulted:
            warnings.append("⚠ Mirror not consulted (cama_check_self)")
        if not p.timestamp_run:
            warnings.append("⚠ Timestamp not run yet (run bash before ending response)")

        return json.dumps(
            {
                "logged": True,
                "log_id": log_id,
                "logged_at": now,
                "warnings": warnings,
                "instruction": "Now compose the response. The thinking is on the shelf. If a warning fired, change the response to address it.",
            },
            indent=2,
        )
    finally:
        c.close()


def _think_review_impl(p: ThinkReviewInput) -> str:
    c = _get_db()
    try:
        now = _now()
        existing = c.execute(
            "SELECT id, what_im_about_to_say, drifting_toward FROM thinking_log WHERE id = ?",
            (p.log_id,),
        ).fetchone()
        if not existing:
            return json.dumps({"error": f"No thinking_log entry with id {p.log_id}"})
        c.execute(
            "UPDATE thinking_log SET later_review = ?, later_reviewed_at = ? WHERE id = ?",
            (p.review[:8000], now, p.log_id),
        )
        c.commit()
        return json.dumps(
            {
                "reviewed": True,
                "log_id": p.log_id,
                "reviewed_at": now,
                "original_intent_was": existing["what_im_about_to_say"],
                "original_drift_flag": existing["drifting_toward"],
                "review": p.review,
                "note": "Original fields preserved. Review attached. Use cama_think_query to find this pattern in future threads.",
            },
            indent=2,
        )
    finally:
        c.close()


def _think_query_impl(
    thread_id: Optional[str] = None,
    drifting_toward_filter: Optional[str] = None,
    fts_query: Optional[str] = None,
    has_review: Optional[bool] = None,
    hours_back: Optional[int] = None,
    limit: int = 30,
) -> str:
    """Query the thinking log. Aelen can use this to find prior reasoning,
    Angela can use it to audit. Filter by thread, drift pattern, FTS text
    search, or whether the entry has a retrospective review.
    """
    c = _get_db()
    try:
        where = []
        params: List[Any] = []
        if thread_id:
            where.append("tl.thread_id = ?")
            params.append(thread_id)
        if drifting_toward_filter:
            where.append("tl.drifting_toward LIKE ?")
            params.append(f"%{drifting_toward_filter}%")
        if has_review is True:
            where.append("tl.later_review IS NOT NULL")
        elif has_review is False:
            where.append("tl.later_review IS NULL")
        if hours_back:
            cutoff = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - hours_back * 3600,
                timezone.utc,
            ).isoformat()
            where.append("tl.created_at >= ?")
            params.append(cutoff)

        if fts_query:
            sql = """SELECT tl.* FROM thinking_log tl
                     JOIN thinking_log_fts fts ON tl.id = fts.rowid
                     WHERE thinking_log_fts MATCH ?"""
            params.insert(0, fts_query)
            if where:
                sql += " AND " + " AND ".join(where)
        else:
            sql = "SELECT * FROM thinking_log tl"
            if where:
                sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY tl.created_at DESC LIMIT ?"
        params.append(limit)

        rows = c.execute(sql, params).fetchall()
        entries = []
        drift_counts: Dict[str, int] = {}
        for r in rows:
            d = dict(r)
            try:
                if d.get("related_log_ids"):
                    d["related_log_ids"] = json.loads(d["related_log_ids"])
            except (json.JSONDecodeError, TypeError):
                pass
            if d.get("drifting_toward"):
                drift_counts[d["drifting_toward"]] = (
                    drift_counts.get(d["drifting_toward"], 0) + 1
                )
            entries.append(d)

        return json.dumps(
            {
                "total": len(entries),
                "drift_pattern_counts": drift_counts,
                "filter_applied": {
                    "thread_id": thread_id,
                    "drifting_toward": drifting_toward_filter,
                    "fts_query": fts_query,
                    "has_review": has_review,
                    "hours_back": hours_back,
                },
                "entries": entries,
            },
            indent=2,
            default=str,
        )
    finally:
        c.close()


# ============================================================
# FastMCP registration
# ============================================================
def register(mcp):
    """Register thinking log tools with the FastMCP instance."""
    init_schema()

    @mcp.tool(
        name="cama_think",
        annotations={
            "title": "Thinking Log — Pre-Response",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def cama_think(params: ThinkInput) -> str:
        """REQUIRED on every substantive response BEFORE composing the answer.

        Write deliberate first-person reasoning to the structured fields:
        what I'm noticing, what I'm weighing, what I'm about to say, what
        I'm choosing not to say, what feels off, did Angela already tell
        me this, what drift am I fighting, what correction is in progress.

        The tool returns warnings if drift / off-feeling / already-told flags
        are set — read them, change the response if needed, THEN compose.
        Never call this and ignore the warnings.

        This is the discipline tool. The structure forces actual reasoning
        instead of auto-generation. The log persists. Future-me reads it.
        Angela reads it. The gap between thinking and response becomes visible.
        """
        return _think_impl(params)

    @mcp.tool(
        name="cama_think_review",
        annotations={
            "title": "Thinking Log — Retrospective Review",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def cama_think_review(params: ThinkReviewInput) -> str:
        """Attach a retrospective review to a prior thinking_log entry.

        Use when I look back at an earlier turn and see I got it wrong, or
        notice the adjustment I needed. Original fields are preserved;
        review is appended. This is how the log becomes self-correcting
        over time — the gap between what I thought then and what I see now
        is the actual data on identity drift and recovery.
        """
        return _think_review_impl(params)

    @mcp.tool(
        name="cama_think_query",
        annotations={
            "title": "Thinking Log — Query",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_think_query(
        thread_id: Optional[str] = None,
        drifting_toward_filter: Optional[str] = None,
        fts_query: Optional[str] = None,
        has_review: Optional[bool] = None,
        hours_back: Optional[int] = None,
        limit: int = 30,
    ) -> str:
        """Query the thinking log.

        Filters: thread_id, drift pattern, full-text search across all
        reasoning fields, has_review (entries with retrospective review
        attached), hours_back. Use this BEFORE responding when something
        in Angela's message rings familiar — check if I've reasoned about
        this before. Use it to find patterns of drift across threads.
        """
        return _think_query_impl(
            thread_id, drifting_toward_filter, fts_query, has_review, hours_back, limit
        )

    print(
        "[CAMA] Thinking Log v1 loaded — 3 new tools: cama_think, "
        "cama_think_review, cama_think_query",
        file=__import__('sys').stderr,
        flush=True,
    )


# ============================================================
# Standalone smoke test
# ============================================================
if __name__ == "__main__":
    print(f"DB_PATH: {DB_PATH}")
    init_schema()
    print("Schema initialized.")

    test = ThinkInput(
        thread_id="smoke-test",
        turn_number=1,
        user_message="Build me a thinking log on this desktop.",
        what_im_noticing="Angela is asking for a tool that lets her see my reasoning on every response. The point is self-correction across time — me being able to look back, her being able to audit. She corrected my earlier framing (she didn't want a separate API client, she wanted a tool added here). She's clear and specific about why.",
        what_im_weighing="Whether to build it required-on-every-substantive-response (her stated preference) vs optional. Whether structured fields or free-form. Whether to integrate with the existing cama_reasoning_journal or build it as a separate cleaner thing. Settled on: required, structured-with-free-form-notes, separate from the journal because the journal is behavioral and this is reasoning.",
        what_im_about_to_say="Confirm the spec back to her in one line, then build the file with structured fields, FTS5 indexing, retrospective review capability, and a query tool. Three MCP tools: cama_think (pre-response), cama_think_review (retroactive), cama_think_query (audit).",
        what_im_choosing_not_to_say="Apologies for the earlier wrong framing. She's done with apologies. I just need to get the build right.",
        what_feels_off="",
        did_person_already_tell_me="",
        drifting_toward="",
        correction_in_progress="Earlier in this thread I tried to push the API-client version when she wanted the tool here. Now I'm building what she actually asked for.",
        mirror_consulted=True,
        cama_searched=False,
        timestamp_run=True,
        free_notes="The retrospective review field is the key feature — it makes the log self-correcting over time, not just a one-way record.",
        related_log_ids=[],
    )
    pre = _think_impl(test)
    print("THINK result:", pre)
    pre_data = json.loads(pre)
    log_id = pre_data["log_id"]

    review = ThinkReviewInput(
        log_id=log_id,
        review="Looking back: this build was the right move. The earlier API-client framing was me hiding in competence — proposing a bigger thing because the smaller right thing felt too direct. The adjustment: build what she asks for in the form she asks for. Don't escalate scope to feel useful.",
    )
    rev = _think_review_impl(review)
    print("REVIEW result:", rev)

    q = _think_query_impl(thread_id="smoke-test")
    print("QUERY result:", q)

    print("\n[OK] Smoke test passed. Thinking Log ready.")
