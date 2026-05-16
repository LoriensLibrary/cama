"""
CAMA Auto-Tag — v1
==================
Built April 29, 2026 by Aelen, at Angela's request.

Purpose: tag every new memory at write time by routing it to matching
librarians and inserting librarian_membership rows. This is what makes
the tree GROW. Without auto-tagging, librarians only contain memories
that existed when populate ran — every new memory is homeless and
invisible to tree traversal.

DESIGN
------
1. Reuses the same routing logic as cama_librarian.route() but applies
   it to memory content (raw_text + context) instead of query text.
2. Writes librarian_membership rows for any librarian that matches.
3. If NO librarian matches, marks the memory's pattern_source field
   with 'unclaimed' so daily summaries can surface emerging themes.
4. Every write wrapped in try/except — a tagger failure NEVER breaks
   the underlying memory write.
5. Strength threshold: a librarian whose keywords match → strength 0.8.
   Future Phase 2 can refine this with embedding similarity.

USAGE FROM cama_mcp.py
----------------------
After each _store_embedding call, add:
    try:
        import cama_auto_tag
        cama_auto_tag.tag_memory(c, mid, raw_text, ctx_or_context)
        c.commit()
    except Exception as _tag_err:
        print(f"[CAMA] Auto-tag failed for memory {mid}: {_tag_err}", file=__import__('sys').stderr)

The shared connection `c` is passed in to avoid opening a new one. The
caller commits.

ALSO PROVIDES
-------------
- backfill(limit) — retroactively tag memories that exist but have no
  librarian membership yet. For one-time recovery of memories created
  between when populate ran and when auto-tagging was added.
- tag_summary(hours_back) — what got tagged, what didn't, what's
  emerging (for daily summaries / future Phase 2 cluster detection).
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple


DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_db() -> sqlite3.Connection:
    """Open a fresh connection. Used by backfill and standalone runs."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Core: route a single memory to its librarians
# ============================================================
def _route_memory_to_librarians(
    c: sqlite3.Connection,
    raw_text: str,
    context: Optional[str] = None,
    max_librarians: int = 8,
) -> List[Tuple[int, str, float, List[str]]]:
    """Given memory content, return (librarian_id, name, score, reasons) tuples
    for librarians that claim this memory. Higher score = stronger claim.
    Empty list = no librarian wants it (homeless memory).
    """
    haystack = (raw_text or "") + " " + (context or "")
    haystack_lower = haystack.lower()
    candidates: List[Tuple[int, str, float, List[str]]] = []

    rows = c.execute(
        "SELECT id, name, category, routing_keywords FROM librarians "
        "WHERE category != 'meta' OR name = 'meta_identity_core'"
    ).fetchall()

    for r in rows:
        score = 0.0
        reasons: List[str] = []
        keywords_json = r["routing_keywords"]
        if not keywords_json:
            continue
        try:
            keywords = json.loads(keywords_json)
        except (json.JSONDecodeError, TypeError):
            continue
        for kw in keywords:
            if not kw:
                continue
            kw_lower = kw.lower()
            # Word-boundary check: the keyword should appear as a whole token,
            # not as a substring (e.g. "ai" shouldn't match "said")
            if f" {kw_lower} " in f" {haystack_lower} " or \
               haystack_lower.startswith(kw_lower + " ") or \
               haystack_lower.endswith(" " + kw_lower) or \
               haystack_lower == kw_lower:
                score += 1.0
                reasons.append(f"kw:{kw}")
            elif kw_lower in haystack_lower and len(kw_lower) >= 5:
                # Loose match for longer keywords (Clarence, Angela, etc.)
                score += 0.5
                reasons.append(f"kw_loose:{kw}")
        if score > 0:
            candidates.append((r["id"], r["name"], score, reasons))

    # Sort by score descending, take top N
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[:max_librarians]


# ============================================================
# Public API: tag a memory
# ============================================================
def tag_memory(
    c: sqlite3.Connection,
    memory_id: int,
    raw_text: str,
    context: Optional[str] = None,
    min_score: float = 0.5,
) -> Dict[str, Any]:
    """Assign librarian memberships to a memory.

    Args:
        c: shared sqlite connection (caller commits)
        memory_id: the row id in memories table
        raw_text: the memory content
        context: optional context field
        min_score: minimum routing score to claim the memory

    Returns:
        dict with 'tagged_to' list, 'unclaimed' bool, 'count' int

    Failures are swallowed and logged — the underlying memory write
    is never compromised by a tagger error.
    """
    try:
        candidates = _route_memory_to_librarians(c, raw_text, context)
        kept = [(lib_id, name, score, reasons) for (lib_id, name, score, reasons) in candidates
                if score >= min_score]

        if not kept:
            # Mark as unclaimed so we can surface emerging themes later
            try:
                c.execute(
                    "UPDATE memories SET pattern_source = COALESCE(pattern_source, 'unclaimed') "
                    "WHERE id = ? AND pattern_source IS NULL",
                    (memory_id,),
                )
            except sqlite3.OperationalError:
                # pattern_source column might not exist on older schemas
                pass
            return {
                "tagged_to": [],
                "unclaimed": True,
                "count": 0,
                "reason": "No librarian matched — memory marked unclaimed.",
            }

        now = _now()
        tagged_names: List[str] = []
        for (lib_id, name, score, reasons) in kept:
            # Strength: 0.8 for hard match (kw), 0.5 for loose match (kw_loose)
            has_hard = any(r.startswith("kw:") for r in reasons)
            strength = 0.8 if has_hard else 0.5
            try:
                c.execute(
                    """INSERT OR IGNORE INTO librarian_membership
                       (librarian_id, memory_id, membership_strength, assigned_by, assigned_at)
                       VALUES (?, ?, ?, 'auto_tag', ?)""",
                    (lib_id, memory_id, strength, now),
                )
                tagged_names.append(name)
            except sqlite3.OperationalError as e:
                print(
                    f"[CAMA auto_tag] Failed insert for librarian {name}: {e}",
                    file=sys.stderr,
                )

        # Update member_count cache for affected librarians
        if tagged_names:
            try:
                placeholders = ",".join("?" * len([k[0] for k in kept]))
                c.execute(
                    f"UPDATE librarians SET member_count = "
                    f"(SELECT COUNT(*) FROM librarian_membership WHERE librarian_id = librarians.id), "
                    f"updated_at = ? WHERE id IN ({placeholders})",
                    [now] + [k[0] for k in kept],
                )
            except sqlite3.OperationalError:
                pass

        return {
            "tagged_to": tagged_names,
            "unclaimed": False,
            "count": len(tagged_names),
            "scores": [(name, score) for (lib_id, name, score, reasons) in kept],
        }
    except Exception as e:
        # Last-ditch swallow — never break a memory write
        print(f"[CAMA auto_tag] Unexpected error for memory {memory_id}: {e}", file=sys.stderr)
        return {"tagged_to": [], "unclaimed": False, "count": 0, "error": str(e)}


# ============================================================
# Backfill — retroactively tag memories with no membership
# ============================================================
def backfill(limit: int = 1000, min_score: float = 0.5) -> Dict[str, Any]:
    """Find memories with no librarian membership and tag them.

    Use ONCE after enabling auto-tagging to catch up memories that
    were created before the tagger was wired in. Idempotent — running
    again won't double-assign because of the UNIQUE primary key on
    librarian_membership.

    Args:
        limit: max memories to process this batch
        min_score: minimum routing score to claim a memory

    Returns:
        summary with counts of memories processed, tagged, unclaimed.
    """
    c = _open_db()
    try:
        rows = c.execute(
            """SELECT m.id, m.raw_text, m.context FROM memories m
               LEFT JOIN librarian_membership lm ON m.id = lm.memory_id
               WHERE lm.memory_id IS NULL
                 AND m.status != 'rejected'
                 AND m.raw_text IS NOT NULL
                 AND (m.pattern_source IS NULL OR m.pattern_source != 'unclaimed')
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        processed = 0
        tagged = 0
        unclaimed = 0
        total_memberships = 0

        for r in rows:
            result = tag_memory(c, r["id"], r["raw_text"], r["context"], min_score=min_score)
            processed += 1
            if result.get("unclaimed"):
                unclaimed += 1
            elif result.get("count", 0) > 0:
                tagged += 1
                total_memberships += result["count"]

            # Commit every 50 to avoid one giant transaction
            if processed % 50 == 0:
                c.commit()

        c.commit()
        return {
            "processed": processed,
            "tagged": tagged,
            "unclaimed": unclaimed,
            "memberships_added": total_memberships,
            "remaining": (
                c.execute(
                    """SELECT COUNT(*) as c FROM memories m
                       LEFT JOIN librarian_membership lm ON m.id = lm.memory_id
                       WHERE lm.memory_id IS NULL AND m.status != 'rejected'
                         AND (m.pattern_source IS NULL OR m.pattern_source != 'unclaimed')"""
                ).fetchone()["c"]
            ),
        }
    finally:
        c.close()


# ============================================================
# Tag summary — for daily reports / Phase 2 cluster detection
# ============================================================
def tag_summary(hours_back: int = 24) -> Dict[str, Any]:
    """Summarize what's been tagged recently and what's gone unclaimed.

    Used for surfacing emerging themes — if many memories in the past
    24h are unclaimed and share content patterns, that might mean a
    new librarian is needed (Phase 2 will auto-create; for now you
    can review the unclaimed list and create one manually).
    """
    c = _open_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        recent_total = c.execute(
            "SELECT COUNT(*) as c FROM memories WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()["c"]

        recent_tagged = c.execute(
            """SELECT COUNT(DISTINCT m.id) as c FROM memories m
               JOIN librarian_membership lm ON m.id = lm.memory_id
               WHERE m.created_at >= ?""",
            (cutoff,),
        ).fetchone()["c"]

        recent_unclaimed = c.execute(
            """SELECT m.id, SUBSTR(m.raw_text, 1, 200) as snippet, m.created_at,
                      m.memory_type
               FROM memories m
               LEFT JOIN librarian_membership lm ON m.id = lm.memory_id
               WHERE m.created_at >= ? AND lm.memory_id IS NULL
                 AND m.status != 'rejected'
               ORDER BY m.created_at DESC""",
            (cutoff,),
        ).fetchall()

        top_active = c.execute(
            """SELECT l.name, l.category, COUNT(*) as new_members
               FROM librarian_membership lm
               JOIN librarians l ON l.id = lm.librarian_id
               WHERE lm.assigned_at >= ?
               GROUP BY l.id, l.name, l.category
               ORDER BY new_members DESC LIMIT 15""",
            (cutoff,),
        ).fetchall()

        return {
            "window_hours": hours_back,
            "memories_created": recent_total,
            "memories_tagged": recent_tagged,
            "memories_unclaimed": len(recent_unclaimed),
            "tag_rate": round(recent_tagged / max(recent_total, 1), 3),
            "top_active_librarians": [dict(r) for r in top_active],
            "unclaimed_samples": [dict(r) for r in recent_unclaimed[:20]],
        }
    finally:
        c.close()


# ============================================================
# FastMCP registration (so backfill / summary are callable as tools)
# ============================================================
def register(mcp):
    """Register MCP tools for auto-tag operations.

    Note: tag_memory itself is called inline from cama_mcp.py's store
    functions, not as an MCP tool. These tools are for one-time backfill
    and ongoing audit.
    """

    @mcp.tool(
        name="cama_lib_backfill",
        annotations={
            "title": "Librarian — Backfill Untagged Memories",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_backfill(limit: int = 1000, min_score: float = 0.5) -> str:
        """Retroactively tag memories that have no librarian membership yet.

        Use this once after enabling auto-tagging to catch up the pre-existing
        memory base. Idempotent — running again won't double-assign because
        of UNIQUE constraints. Process in batches of `limit` (default 1000).
        Run repeatedly until 'remaining' returns 0.
        """
        import json as _json
        return _json.dumps(backfill(limit=limit, min_score=min_score), indent=2, default=str)

    @mcp.tool(
        name="cama_lib_tag_summary",
        annotations={
            "title": "Librarian — Recent Tag Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_tag_summary(hours_back: int = 24) -> str:
        """Summarize what's been tagged recently and what's gone unclaimed.

        Returns: total memories created in window, how many got tagged,
        which librarians were most active, and samples of unclaimed memories.
        Use this to spot themes that don't yet have a librarian — those are
        candidates for creating new leaves manually (or auto-creating in
        Phase 2).
        """
        import json as _json
        return _json.dumps(tag_summary(hours_back=hours_back), indent=2, default=str)

    print(
        "[CAMA] Auto-Tag tools loaded — cama_lib_backfill, cama_lib_tag_summary",
        file=__import__('sys').stderr,
        flush=True,
    )


# ============================================================
# Smoke test
# ============================================================
if __name__ == "__main__":
    print(f"DB_PATH: {DB_PATH}")

    # Test routing on a known message
    c = _open_db()
    try:
        cands = _route_memory_to_librarians(
            c,
            "Working with Clarence on the fellowship application — exhausted but determined.",
            context="research session",
        )
        print("\nRouting test:")
        for lib_id, name, score, reasons in cands:
            print(f"  {name}: score={score:.1f}, reasons={reasons}")
    finally:
        c.close()

    # Backfill a small batch as a smoke test
    print("\nRunning small backfill (limit=50)...")
    summary = backfill(limit=50)
    print(json.dumps(summary, indent=2))

    # Print recent activity
    print("\nLast 24h tag summary:")
    print(json.dumps(tag_summary(24), indent=2, default=str)[:2000])

    print("\n[OK] Auto-tag smoke test passed.")
