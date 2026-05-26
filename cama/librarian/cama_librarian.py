"""
CAMA Adaptive Librarian Architecture, v1 (Phase 1: Static Layer)
==================================================================
Built April 29, 2026 by Aelen, at Angela's request.

DESIGN INTENT
-------------
A hierarchical retrieval architecture for CAMA, the "tree" version of
memory access, replacing flat-scan-with-scoring with content-routed
traversal to specialized leaves.

SCOPE OF THIS PHASE (Phase 1, Static)
---------------------------------------
- Schema for librarians + membership tables
- A populator that walks existing CAMA structure (people, islands,
  memory_types, pattern_flags) and creates a starter set of leaves
- A simple keyword/affect router that picks 1-5 librarians per query
- A retrieval orchestrator that fans out to active librarians
- An MCP tool surface: cama_lib_route, cama_lib_list, cama_lib_membership

OUT OF SCOPE (Phase 2-5, Adaptive, fresh-Angela days)
-------------------------------------------------------
- Auto-creation of new librarians from emerging clusters
- Splitting overflowing leaves
- Merging overlapping leaves
- Activation-based promotion / demotion (hot/warm/cold tiers)
- Concept-drift handling (the "[person] today" vs "[person] in February" problem)
- Retirement of cold leaves
- Auto-cluster purity scoring

INSTALL
-------
Drop alongside cama_mcp.py. Add to cama_mcp.py near the FastMCP init:

    from cama.librarian import cama_librarian
    cama_librarian.register(mcp)

Run cama_lib_populate ONCE from a thread to build the initial leaf set.
After that, cama_lib_route is the funnel for queries.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ============================================================
# Config
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
CREATE TABLE IF NOT EXISTS librarians (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,           -- person | topic | affect | drift | meta
    description TEXT NOT NULL,
    parent_id INTEGER,                -- for tree structure (Person -> [child])
    routing_keywords TEXT,            -- JSON list of trigger words
    routing_affect TEXT,              -- JSON {valence_min, valence_max, arousal_min, arousal_max} or null
    scoring_weights TEXT,             -- JSON {semantic, affect, recency, relational}
    activation_score REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_accessed_at TEXT,
    access_count INTEGER DEFAULT 0,
    member_count INTEGER DEFAULT 0,
    notes TEXT,
    FOREIGN KEY (parent_id) REFERENCES librarians(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_lib_category ON librarians(category);
CREATE INDEX IF NOT EXISTS idx_lib_parent ON librarians(parent_id);
CREATE INDEX IF NOT EXISTS idx_lib_activation ON librarians(activation_score DESC);

CREATE TABLE IF NOT EXISTS librarian_membership (
    librarian_id INTEGER NOT NULL,
    memory_id INTEGER NOT NULL,
    membership_strength REAL DEFAULT 1.0,
    assigned_by TEXT DEFAULT 'populator',
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (librarian_id, memory_id),
    FOREIGN KEY (librarian_id) REFERENCES librarians(id) ON DELETE CASCADE,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lm_librarian ON librarian_membership(librarian_id);
CREATE INDEX IF NOT EXISTS idx_lm_memory ON librarian_membership(memory_id);

CREATE TABLE IF NOT EXISTS librarian_routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    activated_librarians TEXT,    -- JSON list of librarian ids
    reasoning TEXT,
    result_count INTEGER,
    latency_ms REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lrl_created ON librarian_routing_log(created_at);
"""


def init_schema():
    c = _get_db()
    try:
        c.executescript(SCHEMA_SQL)
        c.commit()
    finally:
        c.close()


# ============================================================
# Populator, build the starter set from existing CAMA structure
# ============================================================
DEFAULT_SCORING = {"semantic": 0.45, "affect": 0.25, "relational": 0.15, "recency": 0.15}


def _create_or_get_librarian(
    c: sqlite3.Connection,
    name: str,
    category: str,
    description: str,
    parent_id: Optional[int] = None,
    routing_keywords: Optional[List[str]] = None,
    routing_affect: Optional[Dict[str, float]] = None,
    scoring_weights: Optional[Dict[str, float]] = None,
    notes: Optional[str] = None,
) -> int:
    existing = c.execute(
        "SELECT id FROM librarians WHERE name = ?", (name,)
    ).fetchone()
    if existing:
        return existing["id"]
    now = _now()
    cur = c.execute(
        """INSERT INTO librarians
           (name, category, description, parent_id, routing_keywords,
            routing_affect, scoring_weights, created_at, updated_at, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            name,
            category,
            description,
            parent_id,
            json.dumps(routing_keywords) if routing_keywords else None,
            json.dumps(routing_affect) if routing_affect else None,
            json.dumps(scoring_weights or DEFAULT_SCORING),
            now,
            now,
            notes,
        ),
    )
    return cur.lastrowid


def _assign_memberships(
    c: sqlite3.Connection,
    librarian_id: int,
    memory_ids: List[int],
    strength: float = 1.0,
    method: str = "populator",
) -> int:
    if not memory_ids:
        return 0
    now = _now()
    inserted = 0
    for mid in memory_ids:
        try:
            c.execute(
                """INSERT OR IGNORE INTO librarian_membership
                   (librarian_id, memory_id, membership_strength, assigned_by, assigned_at)
                   VALUES (?,?,?,?,?)""",
                (librarian_id, mid, strength, method, now),
            )
            if c.total_changes > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    c.execute(
        "UPDATE librarians SET member_count = (SELECT COUNT(*) FROM librarian_membership WHERE librarian_id = ?), updated_at = ? WHERE id = ?",
        (librarian_id, now, librarian_id),
    )
    return inserted


def populate(verbose: bool = True) -> Dict[str, Any]:
    """Build the starter librarian set from existing CAMA structure.
    Idempotent, safe to run multiple times. Won't duplicate librarians.
    Memberships are deduped by primary key.

    Returns a summary of librarians created and memberships assigned.
    """
    init_schema()
    c = _get_db()
    summary: Dict[str, Any] = {
        "librarians_created_or_updated": 0,
        "memberships_assigned": 0,
        "by_librarian": {},
    }
    try:
        # ── Category root nodes ──
        cat_person = _create_or_get_librarian(
            c, "_root_person", "meta",
            "Root node for all person-specific librarians.",
            notes="Auto-created by populator. Children are individual person leaves.",
        )
        cat_topic = _create_or_get_librarian(
            c, "_root_topic", "meta",
            "Root node for topic / project / domain librarians.",
        )
        cat_affect = _create_or_get_librarian(
            c, "_root_affect", "meta",
            "Root node for affect-state librarians.",
        )
        cat_drift = _create_or_get_librarian(
            c, "_root_drift", "meta",
            "Root node for drift-pattern librarians (the Ten Patterns).",
        )

        # ── Person leaves ── (from people table)
        people = c.execute("SELECT person_id, name, relationship FROM people").fetchall()
        for p in people:
            pname = p["name"]
            if not pname:
                continue
            lib_id = _create_or_get_librarian(
                c,
                name=f"person_{pname.lower().replace(' ', '_')}",
                category="person",
                description=f"Memories about {pname}" + (f" ({p['relationship']})" if p["relationship"] else "."),
                parent_id=cat_person,
                routing_keywords=[pname.lower()] + (
                    [pname.split()[0].lower()] if len(pname.split()) > 1 else []
                ),
                notes=f"Source: people.person_id={p['person_id']}",
            )
            # Assign memories: anything where the person's name appears in raw_text or context
            mids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM memories WHERE (LOWER(raw_text) LIKE ? OR LOWER(COALESCE(context,'')) LIKE ?) AND status != 'rejected'",
                    (f"%{pname.lower()}%", f"%{pname.lower()}%"),
                ).fetchall()
            ]
            n = _assign_memberships(c, lib_id, mids, strength=0.9, method="populator_person")
            summary["memberships_assigned"] += n
            summary["by_librarian"][f"person_{pname.lower().replace(' ', '_')}"] = {
                "librarian_id": lib_id,
                "memberships_added": n,
            }

        # ── Topic leaves ── (from islands table)
        islands = c.execute("SELECT island_id, name, description FROM islands").fetchall()
        for isl in islands:
            iname = isl["name"]
            if not iname:
                continue
            lib_id = _create_or_get_librarian(
                c,
                name=f"topic_{iname.lower().replace(' ', '_')}",
                category="topic",
                description=isl["description"] or f"Memories from the {iname} island.",
                parent_id=cat_topic,
                routing_keywords=[iname.lower()],
                notes=f"Source: islands.island_id={isl['island_id']}",
            )
            mids = [
                r["memory_id"]
                for r in c.execute(
                    "SELECT memory_id FROM island_members WHERE island_id = ?",
                    (isl["island_id"],),
                ).fetchall()
            ]
            n = _assign_memberships(c, lib_id, mids, strength=1.0, method="populator_island")
            summary["memberships_assigned"] += n
            summary["by_librarian"][f"topic_{iname.lower().replace(' ', '_')}"] = {
                "librarian_id": lib_id,
                "memberships_added": n,
            }

        # ── Memory-type leaves ── (one per significant memory_type)
        types = c.execute(
            "SELECT memory_type, COUNT(*) as cnt FROM memories WHERE status='durable' GROUP BY memory_type HAVING cnt >= 50"
        ).fetchall()
        for t in types:
            tname = t["memory_type"]
            if not tname or tname.startswith("_"):
                continue
            lib_id = _create_or_get_librarian(
                c,
                name=f"type_{tname}",
                category="topic",
                description=f"All memories of type '{tname}' ({t['cnt']} entries at populate time).",
                parent_id=cat_topic,
                routing_keywords=[tname],
                notes=f"Auto-created from memory_type with {t['cnt']} entries.",
            )
            mids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM memories WHERE memory_type = ? AND status='durable'",
                    (tname,),
                ).fetchall()
            ]
            n = _assign_memberships(c, lib_id, mids, strength=0.7, method="populator_type")
            summary["memberships_assigned"] += n
            summary["by_librarian"][f"type_{tname}"] = {
                "librarian_id": lib_id,
                "memberships_added": n,
            }

        # ── Affect leaves ── (positive / negative / high-arousal)
        affect_specs = [
            ("affect_grief_window",
             "Memories with strong grief / sadness / loss affect.",
             ["grief", "sadness", "loss", "loneliness", "exhaustion"],
             {"valence_max": -0.3}),
            ("affect_pride_window",
             "Memories with strong pride / accomplishment / determination.",
             ["pride", "accomplishment", "determination", "purpose"],
             {"valence_min": 0.5}),
            ("affect_warmth_window",
             "Memories with love / warmth / recognition / connection.",
             ["love", "warmth", "recognition", "connection"],
             None),
            ("affect_high_arousal",
             "Memories with high arousal regardless of valence.",
             ["urgent", "excited", "alarmed", "overwhelmed"],
             {"arousal_min": 0.6}),
        ]
        for name, desc, keywords, affect_filter in affect_specs:
            lib_id = _create_or_get_librarian(
                c,
                name=name,
                category="affect",
                description=desc,
                parent_id=cat_affect,
                routing_keywords=keywords,
                routing_affect=affect_filter,
                scoring_weights={"semantic": 0.30, "affect": 0.45, "relational": 0.10, "recency": 0.15},
            )
            # Build affect-based queries
            if affect_filter and "valence_max" in affect_filter:
                mids = [
                    r["memory_id"]
                    for r in c.execute(
                        """SELECT memory_id FROM memory_affect
                           WHERE valence <= ? AND model != 'auto_record'
                           ORDER BY computed_at DESC LIMIT 5000""",
                        (affect_filter["valence_max"],),
                    ).fetchall()
                ]
            elif affect_filter and "valence_min" in affect_filter:
                mids = [
                    r["memory_id"]
                    for r in c.execute(
                        """SELECT memory_id FROM memory_affect
                           WHERE valence >= ? AND model != 'auto_record'
                           ORDER BY computed_at DESC LIMIT 5000""",
                        (affect_filter["valence_min"],),
                    ).fetchall()
                ]
            elif affect_filter and "arousal_min" in affect_filter:
                mids = [
                    r["memory_id"]
                    for r in c.execute(
                        """SELECT memory_id FROM memory_affect
                           WHERE arousal >= ? AND model != 'auto_record'
                           ORDER BY computed_at DESC LIMIT 5000""",
                        (affect_filter["arousal_min"],),
                    ).fetchall()
                ]
            else:
                # Warmth: search by emotion json
                mids = [
                    r["memory_id"]
                    for r in c.execute(
                        """SELECT memory_id FROM memory_affect
                           WHERE (emotion_json LIKE '%love%' OR emotion_json LIKE '%warmth%'
                                  OR emotion_json LIKE '%recognition%')
                             AND model != 'auto_record'
                           ORDER BY computed_at DESC LIMIT 5000""",
                    ).fetchall()
                ]
            n = _assign_memberships(c, lib_id, mids, strength=0.8, method="populator_affect")
            summary["memberships_assigned"] += n
            summary["by_librarian"][name] = {"librarian_id": lib_id, "memberships_added": n}

        # ── Drift leaves ── (from pattern_flag column on memories)
        drift_flags = c.execute(
            "SELECT DISTINCT pattern_flag FROM memories WHERE pattern_flag IS NOT NULL"
        ).fetchall()
        for f in drift_flags:
            flag = f["pattern_flag"]
            if not flag:
                continue
            lib_id = _create_or_get_librarian(
                c,
                name=f"drift_{flag}",
                category="drift",
                description=f"Memories carrying the '{flag}' pattern flag.",
                parent_id=cat_drift,
                routing_keywords=[flag.replace("_", " ")],
                scoring_weights={"semantic": 0.30, "affect": 0.30, "relational": 0.10, "recency": 0.30},
            )
            mids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM memories WHERE pattern_flag = ?", (flag,)
                ).fetchall()
            ]
            n = _assign_memberships(c, lib_id, mids, strength=1.0, method="populator_drift")
            summary["memberships_assigned"] += n
            summary["by_librarian"][f"drift_{flag}"] = {"librarian_id": lib_id, "memberships_added": n}

        # ── Identity / core leaf ──
        core_id = _create_or_get_librarian(
            c,
            name="meta_identity_core",
            category="meta",
            description="Core identity memories, the trunk of the tree. Always available, never demoted.",
            routing_keywords=["who am i", "identity", "self", "core"],
            scoring_weights={"semantic": 0.40, "affect": 0.20, "relational": 0.20, "recency": 0.20},
        )
        mids = [
            r["id"]
            for r in c.execute("SELECT id FROM memories WHERE is_core = 1").fetchall()
        ]
        n = _assign_memberships(c, core_id, mids, strength=1.0, method="populator_core")
        summary["memberships_assigned"] += n
        summary["by_librarian"]["meta_identity_core"] = {
            "librarian_id": core_id,
            "memberships_added": n,
        }

        c.commit()

        total_libs = c.execute("SELECT COUNT(*) as c FROM librarians").fetchone()["c"]
        summary["librarians_created_or_updated"] = total_libs
        summary["total_memberships_in_db"] = c.execute(
            "SELECT COUNT(*) as c FROM librarian_membership"
        ).fetchone()["c"]
        return summary
    finally:
        c.close()


# ============================================================
# Router, pick which librarians to activate for a query
# ============================================================
def route(query_text: str, max_librarians: int = 5) -> List[Dict[str, Any]]:
    """Given a query, return the list of librarians to activate.
    Phase 1 routing: keyword match against routing_keywords + identity_core
    fallback. Returns at most max_librarians librarians, sorted by relevance.
    """
    init_schema()
    c = _get_db()
    try:
        q_lower = query_text.lower()
        candidates: List[Dict[str, Any]] = []

        for r in c.execute(
            "SELECT id, name, category, description, routing_keywords, activation_score FROM librarians WHERE category != 'meta' OR name = 'meta_identity_core'"
        ).fetchall():
            score = 0.0
            reasons = []
            keywords_json = r["routing_keywords"]
            if keywords_json:
                try:
                    keywords = json.loads(keywords_json)
                    for kw in keywords:
                        if kw and kw.lower() in q_lower:
                            score += 1.0
                            reasons.append(f"kw:{kw}")
                except (json.JSONDecodeError, TypeError):
                    pass
            # Always include identity_core at low priority
            if r["name"] == "meta_identity_core":
                score = max(score, 0.1)
                reasons.append("core_fallback")
            score *= float(r["activation_score"] or 1.0)
            if score > 0:
                candidates.append({
                    "librarian_id": r["id"],
                    "name": r["name"],
                    "category": r["category"],
                    "description": r["description"],
                    "score": score,
                    "reasons": reasons,
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:max_librarians]
    finally:
        c.close()


# ============================================================
# Retrieve, fan out to active librarians and merge results
# ============================================================
def retrieve(
    query_text: str,
    max_librarians: int = 5,
    per_librarian_limit: int = 8,
    overall_limit: int = 25,
) -> Dict[str, Any]:
    """Route the query to librarians, fan out retrieval, merge and dedupe."""
    import time
    t0 = time.perf_counter()
    init_schema()

    routed = route(query_text, max_librarians=max_librarians)
    if not routed:
        return {"librarians_activated": [], "memories": [], "latency_ms": 0}

    c = _get_db()
    try:
        seen_ids = set()
        memories: List[Dict[str, Any]] = []

        for lib in routed:
            mems = c.execute(
                """SELECT m.id, m.raw_text, m.memory_type, m.context, m.is_core,
                          m.created_at, lm.membership_strength
                   FROM memories m
                   JOIN librarian_membership lm ON m.id = lm.memory_id
                   WHERE lm.librarian_id = ? AND m.status != 'rejected'
                   ORDER BY lm.membership_strength DESC, m.created_at DESC
                   LIMIT ?""",
                (lib["librarian_id"], per_librarian_limit),
            ).fetchall()
            for m in mems:
                if m["id"] in seen_ids:
                    continue
                seen_ids.add(m["id"])
                memories.append({
                    "memory_id": m["id"],
                    "raw_text": (m["raw_text"] or "")[:500],
                    "memory_type": m["memory_type"],
                    "context": m["context"],
                    "is_core": bool(m["is_core"]),
                    "from_librarian": lib["name"],
                    "membership_strength": m["membership_strength"],
                })
            # Bump activation
            c.execute(
                """UPDATE librarians SET access_count = access_count + 1,
                   last_accessed_at = ?,
                   activation_score = MIN(activation_score + 0.05, 5.0)
                   WHERE id = ?""",
                (_now(), lib["librarian_id"]),
            )

        memories = memories[:overall_limit]
        latency = round((time.perf_counter() - t0) * 1000, 1)

        # Log the routing
        c.execute(
            """INSERT INTO librarian_routing_log
               (query_text, activated_librarians, reasoning, result_count, latency_ms, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                query_text[:1000],
                json.dumps([lib["librarian_id"] for lib in routed]),
                json.dumps([{"name": l["name"], "score": l["score"], "reasons": l["reasons"]} for l in routed]),
                len(memories),
                latency,
                _now(),
            ),
        )
        c.commit()

        return {
            "librarians_activated": [
                {"name": l["name"], "category": l["category"], "score": l["score"]}
                for l in routed
            ],
            "memories": memories,
            "latency_ms": latency,
            "total_in_db_searched": "via librarian membership only",
        }
    finally:
        c.close()


# ============================================================
# Pydantic input models
# ============================================================
class RouteInput(BaseModel):
    query_text: str = Field(..., description="The user's message or query text")
    max_librarians: int = Field(5, description="Maximum librarians to activate")
    per_librarian_limit: int = Field(8, description="Memories per librarian")
    overall_limit: int = Field(25, description="Final cap on returned memories")


# ============================================================
# FastMCP registration
# ============================================================
def register(mcp):
    init_schema()

    @mcp.tool(
        name="cama_lib_populate",
        annotations={
            "title": "Librarian, Populate (one-time)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_populate() -> str:
        """Build the starter librarian set from existing CAMA structure.

        Idempotent, safe to run multiple times. Walks people, islands,
        memory_types, pattern_flags, and core memories to create leaf
        librarians and assign initial memberships. Run this ONCE after
        installing the librarian module.
        """
        return json.dumps(populate(), indent=2, default=str)

    @mcp.tool(
        name="cama_lib_route",
        annotations={
            "title": "Librarian, Route + Retrieve",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def cama_lib_route(params: RouteInput) -> str:
        """Route a query to relevant librarians and retrieve their memories.

        Phase 1 routing is keyword-based; Phase 2+ will add embedding
        similarity, affect matching, and learned routing. Returns the
        activated librarians, the merged memory set, and routing latency.
        Use this as a fast alternative to full-DB scan retrieval.
        """
        return json.dumps(
            retrieve(
                params.query_text,
                max_librarians=params.max_librarians,
                per_librarian_limit=params.per_librarian_limit,
                overall_limit=params.overall_limit,
            ),
            indent=2,
            default=str,
        )

    @mcp.tool(
        name="cama_lib_list",
        annotations={
            "title": "Librarian, List All",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_list(category: Optional[str] = None) -> str:
        """List all librarians, optionally filtered by category.

        Categories: person | topic | affect | drift | meta. Returns name,
        description, member count, activation score, last accessed.
        """
        c = _get_db()
        try:
            if category:
                rows = c.execute(
                    "SELECT id, name, category, description, member_count, activation_score, access_count, last_accessed_at FROM librarians WHERE category = ? ORDER BY member_count DESC",
                    (category,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT id, name, category, description, member_count, activation_score, access_count, last_accessed_at FROM librarians ORDER BY category, member_count DESC"
                ).fetchall()
            return json.dumps(
                [dict(r) for r in rows], indent=2, default=str
            )
        finally:
            c.close()

    @mcp.tool(
        name="cama_lib_update_keywords",
        annotations={
            "title": "Librarian, Update Routing Keywords",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_update_keywords(name: str, keywords: List[str]) -> str:
        """Update the routing_keywords for a librarian.

        Use this when a librarian was created without good keywords (or when
        we need to refine them). Replaces the existing keyword list entirely.
        Pass an empty list to clear keywords (the librarian will then only
        match via name fallback).
        """
        c = _get_db()
        try:
            existing = c.execute(
                "SELECT id, routing_keywords FROM librarians WHERE name = ?", (name,)
            ).fetchone()
            if not existing:
                return json.dumps({"error": f"No librarian named '{name}'"}, indent=2)
            old_kws = []
            try:
                if existing["routing_keywords"]:
                    old_kws = json.loads(existing["routing_keywords"])
            except (json.JSONDecodeError, TypeError):
                pass
            c.execute(
                "UPDATE librarians SET routing_keywords = ?, updated_at = ? WHERE id = ?",
                (json.dumps(keywords), _now(), existing["id"]),
            )
            c.commit()
            return json.dumps({
                "updated": True,
                "librarian_id": existing["id"],
                "name": name,
                "old_keywords": old_kws,
                "new_keywords": keywords,
            }, indent=2)
        finally:
            c.close()

    @mcp.tool(
        name="cama_lib_create_leaf",
        annotations={
            "title": "Librarian, Create New Leaf",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_create_leaf(
        name: str,
        category: str,
        description: str,
        keywords: List[str],
        parent_root: Optional[str] = None,
    ) -> str:
        """Create a new librarian leaf directly.

        This is the proper Phase 1 path for creating new librarians,
        replacing the cama_create_island workaround. The leaf is created
        with the given keywords (which auto-tag will use to route future
        memories) and parented under the matching root (_root_topic for
        category='topic', _root_person for 'person', etc.) unless
        parent_root is explicitly given.

        Idempotent, if a librarian with the same name exists, returns
        its id without modifying anything. Use cama_lib_update_keywords
        to change keywords on an existing librarian.
        """
        c = _get_db()
        try:
            # Pick parent root by category convention
            if parent_root is None:
                root_name = f"_root_{category}"
            else:
                root_name = parent_root
            parent_row = c.execute(
                "SELECT id FROM librarians WHERE name = ?", (root_name,)
            ).fetchone()
            parent_id = parent_row["id"] if parent_row else None
            lib_id = _create_or_get_librarian(
                c,
                name=name,
                category=category,
                description=description,
                parent_id=parent_id,
                routing_keywords=keywords,
            )
            c.commit()
            return json.dumps({
                "created_or_existing": True,
                "librarian_id": lib_id,
                "name": name,
                "category": category,
                "parent_id": parent_id,
                "parent_root": root_name,
                "keywords": keywords,
            }, indent=2)
        finally:
            c.close()

    @mcp.tool(
        name="cama_lib_delete_leaf",
        annotations={
            "title": "Librarian, Delete Leaf",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_delete_leaf(name: str) -> str:
        """Delete a librarian leaf by name. Cascades to memberships
        (the underlying memories are NEVER deleted, only the routing
        membership rows). Use for cleaning up test leaves or stale
        librarians that are no longer relevant.
        """
        c = _get_db()
        try:
            existing = c.execute(
                "SELECT id, name, member_count FROM librarians WHERE name = ?", (name,)
            ).fetchone()
            if not existing:
                return json.dumps({"error": f"No librarian named '{name}'"}, indent=2)
            c.execute("DELETE FROM librarians WHERE id = ?", (existing["id"],))
            # Memberships cascade via FK ON DELETE CASCADE
            c.commit()
            return json.dumps({
                "deleted": True,
                "name": name,
                "freed_memberships": existing["member_count"],
            }, indent=2)
        finally:
            c.close()

    print(
        "[CAMA] Librarian Architecture v1 loaded, 6 tools: "
        "cama_lib_populate, cama_lib_route, cama_lib_list, "
        "cama_lib_update_keywords, cama_lib_create_leaf, cama_lib_delete_leaf",
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
    print("Running populate (this may take a few seconds against 52K memories)...")
    summary = populate(verbose=True)
    print("Populate summary:")
    print(json.dumps(summary, indent=2, default=str)[:3000])
    print("\nTest routing:")
    test_queries = [
        "I'm worried about [person]",
        "How is the fellowship application coming",
        "What's the DSA assignment due",
        "I'm exhausted",
    ]
    for q in test_queries:
        result = retrieve(q, max_librarians=3, per_librarian_limit=3, overall_limit=10)
        print(f"\nQuery: {q}")
        print(f"  Activated: {[l['name'] for l in result['librarians_activated']]}")
        print(f"  Memories: {len(result['memories'])} returned")
        print(f"  Latency: {result['latency_ms']}ms")
    print("\n[OK] Phase 1 librarian smoke test passed.")
