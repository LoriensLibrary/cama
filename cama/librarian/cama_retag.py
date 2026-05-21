"""
CAMA Retag — Phase 1.5 retroactive librarian population.
========================================================
Built April 29, 2026 by Aelen, at Angela's request.

PURPOSE
-------
When you create a NEW librarian (or update an existing one's keywords),
older memories don't automatically get tagged into it because auto-tag
only runs at write time. This module provides retroactive tagging:
walk the corpus, run the new librarian's keywords against each memory,
and assign memberships where they match.

DESIGN
------
- retag_for_librarian(name): Process all memories against ONE librarian's
  keywords. Adds memberships only for new matches (uses INSERT OR IGNORE).
- retag_all_unclaimed(): Re-run tagging on memories marked as 'unclaimed'
  to give them another chance after new librarians are created.
- The functions use the same word-boundary keyword matching as the
  auto-tag at write time, so behavior is consistent.

USAGE
-----
After creating a new librarian with cama_lib_create_leaf or after updating
keywords with cama_lib_update_keywords, call cama_lib_retag_leaf(name) to
populate it from existing memories.

INSTALL
-------
Add to cama_mcp.py near the other CAMA module loads:
    try:
        import cama_retag
        cama_retag.register(mcp)
        print("[CAMA] Retag tools loaded", file=__import__('sys').stderr)
    except Exception as _rt_err:
        print(f"[CAMA] Retag not loaded: {_rt_err}", file=__import__('sys').stderr)
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _keyword_matches(text: str, keywords: List[str]) -> tuple:
    """Word-boundary match. Returns (score, matched_keywords).

    Same semantics as cama_auto_tag._route_memory_to_librarians:
    - exact word boundary match: +1.0
    - loose substring match for keywords >=5 chars: +0.5
    """
    if not text or not keywords:
        return 0.0, []
    haystack_lower = text.lower()
    haystack_padded = f" {haystack_lower} "
    score = 0.0
    matched = []
    for kw in keywords:
        if not kw:
            continue
        kw_lower = kw.lower()
        if (
            f" {kw_lower} " in haystack_padded
            or haystack_lower.startswith(kw_lower + " ")
            or haystack_lower.endswith(" " + kw_lower)
            or haystack_lower == kw_lower
        ):
            score += 1.0
            matched.append(kw)
        elif kw_lower in haystack_lower and len(kw_lower) >= 5:
            score += 0.5
            matched.append(f"{kw}~loose")
    return score, matched


def retag_for_librarian(
    name: str,
    min_score: float = 0.5,
    batch_commit: int = 500,
) -> Dict[str, Any]:
    """Walk all durable memories, score each against the named librarian's
    keywords, and add membership where score >= min_score.

    Args:
        name: librarian name to populate
        min_score: minimum keyword score to claim a memory
        batch_commit: commit every N inserts to avoid one giant transaction

    Returns:
        summary with counts of memories scanned, matched, newly-assigned.
    """
    c = _open_db()
    try:
        lib = c.execute(
            "SELECT id, name, routing_keywords FROM librarians WHERE name = ?",
            (name,),
        ).fetchone()
        if not lib:
            return {"error": f"No librarian named '{name}'"}

        try:
            keywords = json.loads(lib["routing_keywords"]) if lib["routing_keywords"] else []
        except (json.JSONDecodeError, TypeError):
            keywords = []
        if not keywords:
            return {
                "error": f"Librarian '{name}' has no routing_keywords. "
                f"Use cama_lib_update_keywords first."
            }

        librarian_id = lib["id"]

        # Fetch all eligible memories. Stream to keep memory low.
        existing_members = set(
            r["memory_id"]
            for r in c.execute(
                "SELECT memory_id FROM librarian_membership WHERE librarian_id = ?",
                (librarian_id,),
            ).fetchall()
        )

        scanned = 0
        matched = 0
        added = 0
        skipped_already_member = 0
        now = _now()

        rows = c.execute(
            """SELECT id, raw_text, context FROM memories
               WHERE status != 'rejected' AND raw_text IS NOT NULL"""
        )
        for r in rows:
            scanned += 1
            mid = r["id"]
            text = (r["raw_text"] or "") + " " + (r["context"] or "")
            score, kws = _keyword_matches(text, keywords)
            if score < min_score:
                continue
            matched += 1
            if mid in existing_members:
                skipped_already_member += 1
                continue
            # Insert membership. Strength: 0.7 if has any hard match, else 0.5.
            has_hard = any(not k.endswith("~loose") for k in kws)
            strength = 0.7 if has_hard else 0.5
            try:
                c.execute(
                    """INSERT OR IGNORE INTO librarian_membership
                       (librarian_id, memory_id, membership_strength, assigned_by, assigned_at)
                       VALUES (?, ?, ?, 'retag', ?)""",
                    (librarian_id, mid, strength, now),
                )
                added += 1
                if added % batch_commit == 0:
                    c.commit()
            except sqlite3.OperationalError as e:
                print(f"[retag] insert failed for memory {mid}: {e}", file=sys.stderr)

        # Update member_count cache
        try:
            c.execute(
                """UPDATE librarians SET member_count =
                   (SELECT COUNT(*) FROM librarian_membership WHERE librarian_id = ?),
                   updated_at = ? WHERE id = ?""",
                (librarian_id, now, librarian_id),
            )
        except sqlite3.OperationalError:
            pass
        c.commit()

        return {
            "librarian": name,
            "librarian_id": librarian_id,
            "keywords": keywords,
            "memories_scanned": scanned,
            "memories_matched": matched,
            "memberships_added": added,
            "already_members_skipped": skipped_already_member,
            "min_score": min_score,
        }
    finally:
        c.close()


def retag_all_unclaimed(
    min_score: float = 0.5,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Re-run full tagging on memories currently marked 'unclaimed'.

    Useful after creating new librarians: previously-homeless memories
    might now have a home. Clears 'unclaimed' marker on memories that
    successfully match at least one librarian this pass.

    Args:
        min_score: minimum score for a librarian to claim a memory
        limit: max memories to process this batch

    Returns:
        summary of how many got reclaimed and how many remain unclaimed.
    """
    c = _open_db()
    try:
        # Load all librarians' keyword lists once
        libs = c.execute(
            "SELECT id, name, routing_keywords FROM librarians "
            "WHERE category != 'meta' OR name = 'meta_identity_core'"
        ).fetchall()
        lib_kws: List[tuple] = []
        for L in libs:
            try:
                kws = json.loads(L["routing_keywords"]) if L["routing_keywords"] else []
            except (json.JSONDecodeError, TypeError):
                kws = []
            if kws:
                lib_kws.append((L["id"], L["name"], kws))

        rows = c.execute(
            """SELECT id, raw_text, context FROM memories
               WHERE pattern_source = 'unclaimed'
                 AND status != 'rejected'
                 AND raw_text IS NOT NULL
               LIMIT ?""",
            (limit,),
        ).fetchall()

        now = _now()
        scanned = 0
        reclaimed = 0
        still_homeless = 0
        memberships_added = 0

        for r in rows:
            scanned += 1
            mid = r["id"]
            text = (r["raw_text"] or "") + " " + (r["context"] or "")
            matched_libs = []
            for (lib_id, lib_name, kws) in lib_kws:
                score, _ = _keyword_matches(text, kws)
                if score >= min_score:
                    matched_libs.append((lib_id, score))

            if not matched_libs:
                still_homeless += 1
                continue

            for lib_id, score in matched_libs:
                strength = 0.7 if score >= 1.0 else 0.5
                try:
                    cur = c.execute(
                        """INSERT OR IGNORE INTO librarian_membership
                           (librarian_id, memory_id, membership_strength, assigned_by, assigned_at)
                           VALUES (?, ?, ?, 'retag_unclaimed', ?)""",
                        (lib_id, mid, strength, now),
                    )
                    if cur.rowcount > 0:
                        memberships_added += 1
                except sqlite3.OperationalError as e:
                    print(f"[retag_unclaimed] insert failed for memory {mid} -> {lib_id}: {e}", file=sys.stderr)

            # Clear unclaimed marker since we found a home
            try:
                c.execute(
                    "UPDATE memories SET pattern_source = NULL "
                    "WHERE id = ? AND pattern_source = 'unclaimed'",
                    (mid,),
                )
                reclaimed += 1
            except sqlite3.OperationalError:
                pass

        c.commit()

        return {
            "memories_scanned": scanned,
            "memories_reclaimed": reclaimed,
            "memberships_added": memberships_added,
            "still_homeless": still_homeless,
            "min_score": min_score,
            "limit": limit,
        }
    finally:
        c.close()


# ============================================================
# FastMCP registration
# ============================================================
def register(mcp):
    """Register retag tools with the FastMCP instance."""

    @mcp.tool(
        name="cama_lib_retag_leaf",
        annotations={
            "title": "Librarian — Retag for One Leaf",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_retag_leaf(name: str, min_score: float = 0.5) -> str:
        """Retroactively populate a librarian leaf from existing memories.

        Walks all durable memories, matches their content against the
        librarian's routing_keywords, and adds memberships for matches.
        Idempotent — won't double-assign existing members.

        Use after creating a new librarian via cama_lib_create_leaf or
        after updating keywords via cama_lib_update_keywords.
        """
        return json.dumps(
            retag_for_librarian(name, min_score=min_score),
            indent=2,
            default=str,
        )

    @mcp.tool(
        name="cama_lib_retag_unclaimed",
        annotations={
            "title": "Librarian — Retag Unclaimed Pile",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_retag_unclaimed(min_score: float = 0.5, limit: int = 5000) -> str:
        """Re-run tagging on memories currently marked 'unclaimed'.

        After adding new librarians, previously-homeless memories may
        now have homes. This rescans the unclaimed pile and assigns
        them to any matching librarians, clearing the unclaimed marker
        when at least one librarian claims them.
        """
        return json.dumps(
            retag_all_unclaimed(min_score=min_score, limit=limit),
            indent=2,
            default=str,
        )

    print(
        "[CAMA] Retag tools loaded — cama_lib_retag_leaf, cama_lib_retag_unclaimed",
        file=__import__('sys').stderr,
        flush=True,
    )
