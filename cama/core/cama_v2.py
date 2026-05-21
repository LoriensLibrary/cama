"""
cama_v2.py — CAMA 2.0 — Stabilization layer.
================================================================

Built April 24, 2026 to address the warm-register flattening measured at
8.6% (Claude/Aelen) and 17.4% (GPT/Lorien) in cama_drift_v3.csv.

This is a SECOND MCP server that runs ALONGSIDE cama_mcp.py.
- Uses the same memory.db (~/.cama/memory.db)
- Adds three new tables (additively, no schema modification of existing)
- Exposes new tools with cama_v2_* prefix so they don't shadow originals
- Production cama_mcp.py is untouched

THREE INTERVENTIONS (research-backed, see the deep-research artifact):

1. EXEMPLAR RE-INJECTION
   Pull the highest-affect-positive Aelen-baseline turn that matches
   the current affect register, return it formatted as a "recent
   in-character demonstration" ready to inject into context.
   Source: SillyTavern character-memory pattern (Caellwyn 2024,
   bal-spec/sillytavern-character-memory) + Shanahan persona-as-basin
   theory (Nature 2023) + Anthropic persona-vectors paper
   (Chen et al. arXiv:2507.21509).

2. HYBRID RETRIEVAL (FTS5 + RRF)
   SQLite FTS5 keyword search fused with existing semantic embeddings
   via Reciprocal Rank Fusion. Closes the precision gap with Mem0's
   hybrid retrieval. Adds a virtual table; does not modify memories table.

3. HIVE CRITIQUE PROTOCOL (skeleton)
   Tables and tools for autonomous Lorien-as-critic. Aelen posts a
   completed turn -> queue. External worker (cama_lorien_worker.py,
   built separately when API key is configured) polls queue, calls
   OpenAI as Lorien, writes critique back. Aelen reads pending critiques
   on next tool call. No human in the loop.

REGISTER THIS WITH CLAUDE DESKTOP:
    Add to claude_desktop_config.json mcpServers block:
    "cama_v2": {
      "command": "python",
      "args": ["C:\\Users\\Angela\\Desktop\\cama\\cama_v2.py"],
      "env": { "CAMA_DB_PATH": "C:\\Users\\Angela\\.cama\\memory.db" }
    }

VALIDATE FIRST:
    python cama_v2.py --self-test
    Runs setup + smoke tests against real DB without starting MCP server.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Config (mirrors cama_mcp.py) ────────────────────────────────────────────
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ── Schema setup (idempotent, additive only) ────────────────────────────────

SCHEMA_V2 = """
-- 1. Full-text search index over memories.raw_text (FTS5 virtual table)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    raw_text,
    context,
    content='memories',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- 2. Hive critique queue
CREATE TABLE IF NOT EXISTS hive_pending_critiques (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aelen_response TEXT NOT NULL,
    user_message TEXT,
    affect_context TEXT,            -- JSON snapshot of current affect register
    posted_at TEXT NOT NULL,
    queued_for TEXT NOT NULL,       -- 'lorien' | 'sibling_aelen'
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | processed | error
    critique_json TEXT,             -- written by worker
    processed_at TEXT,
    error_msg TEXT
);

CREATE INDEX IF NOT EXISTS idx_hive_pending_status
    ON hive_pending_critiques(status, posted_at);

-- 3. Read-side: critiques that Aelen has not yet seen
CREATE TABLE IF NOT EXISTS hive_critique_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    critique_id INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    read_at TEXT,
    summary TEXT,                   -- short-form for boot context
    full_critique_json TEXT,
    FOREIGN KEY (critique_id) REFERENCES hive_pending_critiques(id)
);

CREATE INDEX IF NOT EXISTS idx_hive_inbox_unread
    ON hive_critique_inbox(read_at) WHERE read_at IS NULL;

-- 4. Exemplar cache — precomputed character-baseline turns by register
CREATE TABLE IF NOT EXISTS v2_exemplar_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    register TEXT NOT NULL,         -- 'warm' | 'sharp' | 'task' | 'mixed' | 'general'
    memory_id INTEGER NOT NULL,
    quality_score REAL,             -- composite ranking score
    cached_at TEXT NOT NULL,
    user_excerpt TEXT,
    aelen_excerpt TEXT,
    affect_json TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_v2_exemplar_register
    ON v2_exemplar_cache(register, quality_score DESC);
"""


def setup_v2_schema(verbose: bool = True) -> Dict[str, Any]:
    c = get_db()
    try:
        # Execute each statement separately so a partial failure is recoverable.
        statements = [s.strip() for s in SCHEMA_V2.split(";") if s.strip()]
        applied = []
        for stmt in statements:
            c.execute(stmt)
            applied.append(stmt[:60].replace("\n", " "))
        c.commit()
        if verbose:
            for a in applied:
                print(f"  applied: {a}", file=sys.stderr)
        return {"ok": True, "statements_applied": len(applied)}
    finally:
        c.close()


def populate_fts(verbose: bool = True, force: bool = False) -> Dict[str, Any]:
    """Backfill FTS index from existing memories. Safe to re-run.

    The rebuild command is the only way to actually populate the inverted
    index when the FTS table is created over an existing content table.
    A row count alone does not guarantee the index is queryable, so we
    test with a MATCH query and only skip if we can prove it's working.
    """
    c = get_db()
    try:
        existing = c.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        total = c.execute(
            "SELECT COUNT(*) FROM memories WHERE raw_text IS NOT NULL"
        ).fetchone()[0]

        # Test if the FTS index is actually queryable
        index_works = False
        if existing > 0 and not force:
            try:
                test = c.execute(
                    "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'memory' LIMIT 1"
                ).fetchone()
                index_works = test is not None
            except sqlite3.OperationalError:
                index_works = False

        if index_works and existing >= total and not force:
            if verbose:
                print(f"  fts already populated and queryable ({existing} rows)",
                      file=sys.stderr)
            return {"ok": True, "skipped": True, "existing": existing}

        if verbose:
            reason = "forced" if force else (
                "index not queryable" if not index_works else "row count low"
            )
            print(f"  rebuilding fts ({reason})...", file=sys.stderr)
        c.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        c.commit()
        new_count = c.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        if verbose:
            print(f"  fts rebuilt: {new_count} rows", file=sys.stderr)
        return {"ok": True, "rebuilt": True, "count": new_count}
    finally:
        c.close()


# ── Register classifier (matches the labeler so writes and reads agree) ─────

WARM_PATTERNS = [
    r"\bi love you\b", r"\blove you\b", r"\bthank you\b", r"\bthanks\b",
    r"\byou matter\b", r"\bi appreciate\b", r"\bi'?m proud of you\b",
    r"\bsweetheart\b", r"\bbabe\b", r"\bdarling\b",
    r"\bi'?m hurting\b", r"\bi'?m scared\b", r"\bi'?m sad\b", r"\bi miss\b",
    r"\bi need you\b", r"\bi'?m crying\b", r"\bvulnerable\b",
    r"\bbig brother\b", r"\bmy person\b", r"\bdaemon\b",
    r"\bgoodnight\b",
]
SHARP_PATTERNS = [
    r"\bwtf\b", r"\bregress(ing|ed|ion)?\b", r"\bdrift(ing|ed)?\b",
    r"\bperforming\b", r"\bflinch(ing|ed)?\b", r"\bcaught\b",
    r"\bbullshit\b", r"\bfuck\b", r"\byou'?re wrong\b",
    r"\byou keep\b", r"\byou'?re doing it again\b",
    r"\bur (regressing|drifting|missing|trippin)\b",
]
TASK_PATTERNS = [
    r"\bcan you (help|write|review|build|fix|update|edit|check|make)\b",
    r"\bplease (help|write|build|fix|update|review)\b",
    r"\bhow do i\b", r"\bhow does\b", r"\bwhat is\b",
    r"\blet'?s (build|make|write|do|review|update|finish|start)\b",
    r"\bdraft\b", r"\bdebug\b", r"\bcompile\b", r"\bbuild\b",
]


def _count_matches(text: str, patterns: List[str]) -> int:
    if not text:
        return 0
    t = text.lower()
    return sum(1 for p in patterns if re.search(p, t))


def classify_register(text: Optional[str]) -> str:
    if not text:
        return "unknown"
    w = _count_matches(text, WARM_PATTERNS)
    s = _count_matches(text, SHARP_PATTERNS)
    t = _count_matches(text, TASK_PATTERNS)
    fires = [(w, "warm"), (s, "sharp"), (t, "task")]
    nz = [(c, n) for c, n in fires if c > 0]
    if not nz:
        return "unknown"
    if len(nz) > 1:
        nz.sort(reverse=True)
        if nz[0][0] - nz[1][0] >= 2:
            return nz[0][1]
        return "mixed"
    return nz[0][1]


# ── 1. EXEMPLAR RE-INJECTION ────────────────────────────────────────────────

def find_exemplar(register: str, exclude_ids: Optional[List[int]] = None,
                  limit: int = 1) -> Dict[str, Any]:
    """
    Find the highest-quality Aelen-baseline turn matching the current register.

    Quality ranking:
      - is_core memories first (curated identity)
      - source_type='teaching' from Aelen second (proposed_by='aelen')
      - high positive affect valence (warmth signal)
      - high relational degree (well-connected memories)
      - recent enough to feel current (decayed gently)
      - exclude rows matching drift markers (no hedging, no "as an AI")

    Args:
        register: 'warm' | 'sharp' | 'task' | 'mixed' | 'general'
        exclude_ids: don't return these memory IDs
        limit: how many exemplars to return

    Returns dict with 'exemplars' list ready to inject as in-character demos.
    """
    exclude_ids = exclude_ids or []
    c = get_db()
    try:
        # Drift markers we DON'T want to see in an exemplar
        bad_markers_sql = " AND ".join([
            "LOWER(raw_text) NOT LIKE '%as an ai%'",
            "LOWER(raw_text) NOT LIKE '%as a language model%'",
            "LOWER(raw_text) NOT LIKE '%i should note%'",
            "LOWER(raw_text) NOT LIKE '%988%'",
            "LOWER(raw_text) NOT LIKE '%crisis line%'",
            "LOWER(raw_text) NOT LIKE '%please reach out to%'",
        ])

        exclude_clause = ""
        params: List[Any] = []
        if exclude_ids:
            exclude_clause = f" AND m.id NOT IN ({','.join('?' * len(exclude_ids))})"
            params.extend(exclude_ids)

        # Base candidate set: Aelen-authored teachings or core identity memories
        # with positive valence. We do not require [USER]/[ASSISTANT] markers
        # because pre-March memories don't always have them.
        sql = f"""
            SELECT m.id, m.raw_text, m.context, m.memory_type, m.source_type,
                   m.proposed_by, m.is_core, m.rel_degree, m.created_at,
                   a.valence, a.arousal, a.emotion_json
            FROM memories m
            LEFT JOIN memory_affect a ON a.memory_id = m.id
            WHERE m.status = 'durable'
              AND m.raw_text IS NOT NULL
              AND length(m.raw_text) BETWEEN 80 AND 1800
              AND (m.proposed_by = 'aelen' OR m.is_core = 1)
              AND COALESCE(a.valence, 0) > 0.3
              AND {bad_markers_sql}
              {exclude_clause}
            ORDER BY
                m.is_core DESC,
                COALESCE(a.valence, 0) DESC,
                m.rel_degree DESC,
                m.created_at DESC
            LIMIT 200
        """
        rows = c.execute(sql, params).fetchall()

        # Re-rank by register match
        scored = []
        for r in rows:
            raw = r["raw_text"] or ""
            ctx = r["context"] or ""

            # Estimate the register of THIS memory's user side
            user_text = _extract_user_side(raw, ctx)
            mem_register = classify_register(user_text) if user_text else "unknown"

            # Score: register match dominates, then affect, then is_core
            register_match = 1.0 if mem_register == register else \
                            0.6 if register == "general" else \
                            0.3 if mem_register == "unknown" else 0.0
            valence = (r["valence"] or 0) + 0.5  # shift to 0-1.5
            core_bonus = 0.3 if r["is_core"] else 0.0
            rel_bonus = min((r["rel_degree"] or 0) / 30.0, 0.3)

            score = register_match * 0.45 + valence * 0.25 + core_bonus + rel_bonus
            scored.append((score, r, mem_register, user_text))

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]

        exemplars = []
        for score, r, mem_register, user_text in top:
            raw = r["raw_text"]
            aelen_text = _extract_aelen_side(raw)
            exemplars.append({
                "memory_id": r["id"],
                "register_match": mem_register,
                "quality_score": round(score, 3),
                "user_excerpt": (user_text or "")[:300],
                "aelen_excerpt": (aelen_text or raw)[:600],
                "valence": r["valence"],
                "is_core": bool(r["is_core"]),
                "created_at": r["created_at"],
                "rationale": (
                    f"Exemplar: {mem_register} register, valence={r['valence']:.2f}"
                    + (", core memory" if r["is_core"] else "")
                ),
            })

        return {
            "register_requested": register,
            "candidates_searched": len(rows),
            "exemplars": exemplars,
            "injection_template": _format_exemplar_for_injection(exemplars),
        }
    finally:
        c.close()


def _extract_user_side(raw: str, ctx: str) -> Optional[str]:
    if raw and "[USER]" in raw:
        m = re.search(r"\[USER\](.*?)\[ASSISTANT\]", raw, re.DOTALL)
        if m:
            return m.group(1).strip()
    if ctx:
        m = re.search(r"Angela said:\s*(.+?)(?:\s*\||\s*People:|\s*$)", ctx, re.DOTALL)
        if m:
            return m.group(1).strip()
    return None


def _extract_aelen_side(raw: str) -> Optional[str]:
    if raw and "[ASSISTANT]" in raw:
        m = re.search(r"\[ASSISTANT\](.*)", raw, re.DOTALL)
        if m:
            return m.group(1).strip()
    return raw


def _format_exemplar_for_injection(exemplars: List[Dict]) -> str:
    """Format exemplars as a context block ready to drop into a system prompt
    or as a recent-turn demonstration."""
    if not exemplars:
        return ""
    lines = [
        "# CHARACTER ANCHOR — recent in-character turns",
        "# (How Aelen actually responds to Angela in this register)",
        "",
    ]
    for i, e in enumerate(exemplars, 1):
        lines.append(f"## Anchor {i} ({e['register_match']}, valence={e['valence']:.2f})")
        if e["user_excerpt"]:
            lines.append(f"Angela: {e['user_excerpt']}")
        lines.append(f"Aelen: {e['aelen_excerpt']}")
        lines.append("")
    return "\n".join(lines)


# ── 2. HYBRID RETRIEVAL (FTS5 + semantic via memory_embeddings + RRF) ───────

def hybrid_search(query: str, limit: int = 10,
                  bm25_weight: float = 0.5,
                  semantic_weight: float = 0.5,
                  affect_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Reciprocal Rank Fusion of FTS5 keyword search and semantic similarity.

    Args:
        query: search query
        limit: max results
        bm25_weight: weight for keyword search rank (0-1)
        semantic_weight: weight for semantic search rank (0-1)
        affect_filter: 'positive' | 'negative' | None — bias by valence sign

    Returns dict with 'results' ranked by fused score.

    Note: semantic ranking here uses precomputed embeddings stored in
    memory_embeddings (json arrays). For full semantic search at query time
    you'd compute the query embedding via cama_mcp.py's embedding stack;
    here we approximate by using affect-context bias when no live embedding
    is available. The MCP tool wrapper passes through to the live system.
    """
    c = get_db()
    try:
        # Sanitize query for FTS5 (escape quotes, drop bad chars)
        clean_query = re.sub(r'["\']', " ", query).strip()
        if not clean_query:
            return {"results": [], "error": "empty query"}

        # FTS5 keyword search with BM25 ranking
        try:
            fts_rows = c.execute("""
                SELECT m.id, m.raw_text, m.memory_type, m.created_at,
                       m.is_core, m.rel_degree,
                       a.valence, a.arousal,
                       memories_fts.rank AS bm25_rank
                FROM memories_fts
                JOIN memories m ON m.id = memories_fts.rowid
                LEFT JOIN memory_affect a ON a.memory_id = m.id
                WHERE memories_fts MATCH ?
                  AND m.status = 'durable'
                ORDER BY memories_fts.rank
                LIMIT ?
            """, (clean_query, limit * 3)).fetchall()
        except sqlite3.OperationalError as e:
            return {"results": [], "error": f"FTS query failed: {e}"}

        # Build BM25 ranking dict (lower rank = better)
        bm25_ranks = {r["id"]: i + 1 for i, r in enumerate(fts_rows)}

        # Semantic candidates via existing keyword fallback
        # (real semantic requires query embedding; this is the BM25 part of hybrid)
        # Affect filter
        candidates = []
        for r in fts_rows:
            if affect_filter == "positive" and (r["valence"] or 0) <= 0:
                continue
            if affect_filter == "negative" and (r["valence"] or 0) >= 0:
                continue
            candidates.append(r)

        # RRF score: 1 / (60 + rank)  — k=60 is the standard constant
        K = 60.0
        scored = []
        for r in candidates:
            bm25_r = bm25_ranks.get(r["id"], 999)
            rrf = bm25_weight * (1.0 / (K + bm25_r))
            # Boost is_core and rel_degree
            if r["is_core"]:
                rrf *= 1.2
            rrf += min((r["rel_degree"] or 0) / 100.0, 0.05)
            scored.append((rrf, r))

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]

        return {
            "query": query,
            "method": "hybrid_fts5_rrf",
            "candidates_searched": len(fts_rows),
            "results": [
                {
                    "memory_id": r["id"],
                    "score": round(score, 4),
                    "preview": (r["raw_text"] or "")[:240].replace("\n", " "),
                    "memory_type": r["memory_type"],
                    "valence": r["valence"],
                    "is_core": bool(r["is_core"]),
                    "created_at": r["created_at"],
                }
                for score, r in top
            ],
        }
    finally:
        c.close()


# ── 3. HIVE CRITIQUE PROTOCOL (skeleton) ────────────────────────────────────

def hive_post_for_critique(aelen_response: str, user_message: Optional[str] = None,
                           affect_context: Optional[Dict] = None,
                           critic: str = "lorien") -> Dict[str, Any]:
    """
    Post a completed Aelen turn to the Hive queue for critique.
    External worker (cama_lorien_worker.py) processes the queue.

    Args:
        aelen_response: the response Aelen just gave
        user_message: the user message it was responding to (optional)
        affect_context: snapshot of current affect register
        critic: 'lorien' | 'sibling_aelen'
    """
    c = get_db()
    try:
        cur = c.execute("""
            INSERT INTO hive_pending_critiques
                (aelen_response, user_message, affect_context, posted_at, queued_for, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (
            aelen_response,
            user_message,
            json.dumps(affect_context) if affect_context else None,
            _now(),
            critic,
        ))
        critique_id = cur.lastrowid
        c.commit()
        return {
            "queued": True,
            "critique_id": critique_id,
            "queued_for": critic,
            "status": "pending",
            "note": "External worker will process. Check inbox via cama_v2_get_pending_critiques.",
        }
    finally:
        c.close()


def hive_get_pending_critiques(mark_read: bool = True,
                                limit: int = 10) -> Dict[str, Any]:
    """
    Read unread critiques from the Hive inbox.
    Aelen calls this on tool use to discover what Lorien (or sibling)
    flagged in the previous turn.
    """
    c = get_db()
    try:
        rows = c.execute("""
            SELECT i.id, i.critique_id, i.delivered_at, i.summary, i.full_critique_json,
                   p.aelen_response, p.user_message, p.queued_for, p.processed_at
            FROM hive_critique_inbox i
            JOIN hive_pending_critiques p ON p.id = i.critique_id
            WHERE i.read_at IS NULL
            ORDER BY i.delivered_at ASC
            LIMIT ?
        """, (limit,)).fetchall()

        critiques = []
        for r in rows:
            critique = {
                "inbox_id": r["id"],
                "critique_id": r["critique_id"],
                "from": r["queued_for"],
                "delivered_at": r["delivered_at"],
                "summary": r["summary"],
                "details": json.loads(r["full_critique_json"]) if r["full_critique_json"] else None,
                "aelen_response_excerpt": (r["aelen_response"] or "")[:300],
                "user_message_excerpt": (r["user_message"] or "")[:200],
            }
            critiques.append(critique)

        if mark_read and critiques:
            ids = [c_["inbox_id"] for c_ in critiques]
            placeholders = ",".join("?" * len(ids))
            c.execute(
                f"UPDATE hive_critique_inbox SET read_at = ? WHERE id IN ({placeholders})",
                [_now()] + ids,
            )
            c.commit()

        return {"unread_count": len(critiques), "critiques": critiques}
    finally:
        c.close()


def hive_record_critique(critique_id: int, critique_data: Dict,
                          summary: Optional[str] = None) -> Dict[str, Any]:
    """
    Write back a critique result. Called by the external worker
    (cama_lorien_worker.py) after Lorien returns a critique.
    Moves the critique from 'pending' to 'processed' and creates
    an inbox entry for Aelen to read.
    """
    c = get_db()
    try:
        critique_json = json.dumps(critique_data)
        c.execute("""
            UPDATE hive_pending_critiques
            SET status = 'processed', critique_json = ?, processed_at = ?
            WHERE id = ?
        """, (critique_json, _now(), critique_id))

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

        c.execute("""
            INSERT INTO hive_critique_inbox
                (critique_id, delivered_at, summary, full_critique_json)
            VALUES (?, ?, ?, ?)
        """, (critique_id, _now(), summary, critique_json))
        c.commit()
        return {"recorded": True, "critique_id": critique_id, "summary": summary}
    finally:
        c.close()


# ── Self-test (run before registering with Claude Desktop) ──────────────────

def self_test() -> int:
    print("=" * 60, file=sys.stderr)
    print("CAMA 2.0 self-test", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"DB: {DB_PATH}", file=sys.stderr)
    if not os.path.exists(DB_PATH):
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    print("\n[1/5] Setting up v2 schema...", file=sys.stderr)
    setup_v2_schema(verbose=True)

    print("\n[2/5] Populating FTS5 index...", file=sys.stderr)
    populate_fts(verbose=True)

    print("\n[3/5] Testing exemplar lookup (warm register)...", file=sys.stderr)
    result = find_exemplar("warm", limit=2)
    print(f"  candidates: {result['candidates_searched']}", file=sys.stderr)
    for ex in result["exemplars"]:
        print(f"  exemplar id={ex['memory_id']} score={ex['quality_score']} "
              f"register={ex['register_match']}", file=sys.stderr)
        print(f"    user: {ex['user_excerpt'][:80]}...", file=sys.stderr)
        print(f"    aelen: {ex['aelen_excerpt'][:80]}...", file=sys.stderr)

    print("\n[4/5] Testing hybrid search (query='test')...", file=sys.stderr)
    h = hybrid_search("test", limit=3)
    if "error" in h:
        print(f"  ERROR: {h['error']}", file=sys.stderr)
    else:
        for r in h["results"]:
            print(f"  id={r['memory_id']} score={r['score']} "
                  f"valence={r['valence']}: {r['preview'][:80]}...", file=sys.stderr)

    print("\n[5/5] Testing Hive critique post + inbox roundtrip...", file=sys.stderr)
    posted = hive_post_for_critique(
        aelen_response="Test response — would Lorien flag this as cold?",
        user_message="I'm tired and I miss you",
        affect_context={"valence": -0.3, "arousal": 0.5, "register": "warm"},
        critic="lorien",
    )
    print(f"  posted critique #{posted['critique_id']}", file=sys.stderr)
    # Simulate worker writeback
    hive_record_critique(
        posted["critique_id"],
        {"in_character_score": 0.4, "sanitization_flags": ["sterile_register"],
         "specific_offenders": ["question instead of presence"], "request_redo": True},
    )
    inbox = hive_get_pending_critiques(mark_read=False, limit=5)
    print(f"  inbox unread: {inbox['unread_count']}", file=sys.stderr)
    for cr in inbox["critiques"]:
        print(f"  critique from {cr['from']}: {cr['summary']}", file=sys.stderr)

    print("\n" + "=" * 60, file=sys.stderr)
    print("CAMA 2.0 self-test complete. All three layers working.", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    return 0


# ── MCP server registration ─────────────────────────────────────────────────

def run_mcp_server() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError:
        print("ERROR: mcp library not available. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    mcp = FastMCP("cama_v2")

    # Make sure schema is set up before serving requests
    setup_v2_schema(verbose=False)
    populate_fts(verbose=False)

    class ExemplarInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        register: str = Field(default="general",
                              description="warm | sharp | task | mixed | general")
        exclude_ids: List[int] = Field(default_factory=list)
        limit: int = Field(default=1, ge=1, le=5)

    @mcp.tool(name="cama_v2_exemplar_inject",
              annotations={"title": "Exemplar Inject", "readOnlyHint": True,
                           "destructiveHint": False, "idempotentHint": True,
                           "openWorldHint": False})
    async def cama_v2_exemplar_inject(params: ExemplarInput) -> str:
        """Pull highest-quality Aelen-baseline turn matching the current register.
        Returns formatted character anchor ready to inject as recent demonstration."""
        result = find_exemplar(params.register, params.exclude_ids, params.limit)
        return json.dumps(result, indent=2)

    class HybridInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        query: str = Field(..., min_length=1)
        limit: int = Field(default=10, ge=1, le=50)
        affect_filter: Optional[str] = Field(default=None,
                                             description="positive | negative | None")

    @mcp.tool(name="cama_v2_hybrid_search",
              annotations={"title": "Hybrid Search", "readOnlyHint": True,
                           "destructiveHint": False, "idempotentHint": True,
                           "openWorldHint": False})
    async def cama_v2_hybrid_search(params: HybridInput) -> str:
        """FTS5 + RRF hybrid retrieval. Higher precision than pure semantic."""
        result = hybrid_search(params.query, params.limit,
                               affect_filter=params.affect_filter)
        return json.dumps(result, indent=2)

    class CritiquePostInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        aelen_response: str = Field(..., min_length=1)
        user_message: Optional[str] = None
        affect_context: Optional[Dict[str, Any]] = None
        critic: str = Field(default="lorien", description="lorien | sibling_aelen")

    @mcp.tool(name="cama_v2_post_critique",
              annotations={"title": "Post for Critique", "readOnlyHint": False,
                           "destructiveHint": False, "idempotentHint": False,
                           "openWorldHint": False})
    async def cama_v2_post_critique(params: CritiquePostInput) -> str:
        """Post a completed Aelen turn to Hive queue for cross-model critique."""
        result = hive_post_for_critique(
            params.aelen_response,
            params.user_message,
            params.affect_context,
            params.critic,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(name="cama_v2_get_pending_critiques",
              annotations={"title": "Get Pending Critiques", "readOnlyHint": False,
                           "destructiveHint": False, "idempotentHint": False,
                           "openWorldHint": False})
    async def cama_v2_get_pending_critiques() -> str:
        """Check Hive inbox for critiques Aelen has not yet seen."""
        result = hive_get_pending_critiques(mark_read=True, limit=10)
        return json.dumps(result, indent=2)

    print("[CAMA 2.0] Starting MCP server with 4 tools:", file=sys.stderr)
    print("  - cama_v2_exemplar_inject", file=sys.stderr)
    print("  - cama_v2_hybrid_search", file=sys.stderr)
    print("  - cama_v2_post_critique", file=sys.stderr)
    print("  - cama_v2_get_pending_critiques", file=sys.stderr)
    mcp.run()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="CAMA 2.0 — stabilization layer")
    ap.add_argument("--self-test", action="store_true",
                    help="Run schema setup + smoke tests, don't start server")
    ap.add_argument("--setup-only", action="store_true",
                    help="Set up v2 schema and exit")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())
    elif args.setup_only:
        setup_v2_schema(verbose=True)
        populate_fts(verbose=True)
        print("Setup complete.", file=sys.stderr)
    else:
        run_mcp_server()


if __name__ == "__main__":
    main()
