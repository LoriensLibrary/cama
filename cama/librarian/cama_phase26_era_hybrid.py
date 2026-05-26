"""
CAMA Phase 2.6, Era-Aware Hybrid Routing with Gated Sub-Centroid Boost
========================================================================
Built April 30, 2026 by Aelen, with input from Lorien and Angela's call.

WHAT THIS DOES
--------------
Phase 2's single-centroid routing won the April 29 benchmark (33.4% R@5)
because it acts as a regularizer over the full leaf. Phase 2.5 (naive
sub-centroid clustering) lost (24.8% R@5) because fragmentation destroyed
ranking stability for sparse queries.

Phase 2.6 takes Lorien's framing literally: leaves are stabilized
meaning-fields across time. Sub-centroids should act as controlled
apertures inside the field, not replace it. Two changes:

1. ERA-AWARE SUB-CENTROIDS
   Within each librarian, members are bucketed into eras (early /
   middle / recent) based on per-librarian created_at quantiles.
   Sub-centroids are computed within each era separately. A query
   about preschool drop-off matches the appropriate era's cluster
   instead of competing against an averaged historical view.

2. GATED SUB-CENTROID SCORING
   Single-centroid sim is the stabilizer (always contributes).
   Max sub-centroid sim is a BOOST that only fires when:
     - margin between top-1 and top-2 sub-centroid is above threshold
     - the matched cluster has adequate local density
     - the query has enough content to land somewhere specific
   On sparse queries (verbatim keyword extracts), gating closes and
   we fall back to single-centroid behavior.

3. WRONG-CONFIDENCE TELEMETRY
   Routing returns not just rankings but per-query confidence (top-1
   margin). The eval harness records confidence-band breakdowns so we
   can distinguish "low-confidence miss" from "high-confidence wrong
   route", those are different failure modes with different costs.

DEFAULT KNOBS (tune empirically)
--------------------------------
EMBEDDING_WEIGHT      = 5.0  (carryover from Phase 2)
ALPHA_BOOST           = 0.6  (sub-centroid contribution when gate open)
GATE_MARGIN_MIN       = 0.05 (min top-1/top-2 cosine margin in cluster)
GATE_DENSITY_MIN      = 5    (min cluster size to be eligible)
GATE_QUERY_TOKEN_MIN  = 6    (min query content tokens to be eligible)
ERA_QUANTILES         = [0.33, 0.67]  (split early/middle/recent)
"""

import json
import math
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

# Knobs
EMBEDDING_WEIGHT      = 5.0
ALPHA_BOOST           = 0.6
GATE_MARGIN_MIN       = 0.05
GATE_DENSITY_MIN      = 5
GATE_QUERY_TOKEN_MIN  = 6
ERA_QUANTILES         = [0.33, 0.67]


from cama.core.time_utils import now_iso as _now


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Schema, era-tagged sub-centroids
# ============================================================
def init_schema():
    """Create the era_subcentroids table. Different from the Phase 2.5
    table because each row is also tagged with an era bucket."""
    c = _open_db()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS librarian_era_subcentroids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                librarian_id INTEGER NOT NULL,
                era TEXT NOT NULL,
                cluster_id INTEGER NOT NULL,
                centroid_embedding TEXT NOT NULL,
                cluster_size INTEGER NOT NULL,
                era_lo_ts TEXT,
                era_hi_ts TEXT,
                computed_at TEXT NOT NULL,
                FOREIGN KEY(librarian_id) REFERENCES librarians(id),
                UNIQUE(librarian_id, era, cluster_id)
            );
            CREATE INDEX IF NOT EXISTS idx_era_sub_lib
                ON librarian_era_subcentroids(librarian_id);
            CREATE INDEX IF NOT EXISTS idx_era_sub_lib_era
                ON librarian_era_subcentroids(librarian_id, era);

            -- per-query confidence telemetry from eval runs
            CREATE TABLE IF NOT EXISTS routing_confidence_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_label TEXT NOT NULL,
                router TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                top1_score REAL,
                top2_score REAL,
                margin REAL,
                gate_open INTEGER,
                top1_correct INTEGER,
                logged_at TEXT NOT NULL
            );
        """)
        c.commit()
    finally:
        c.close()


# ============================================================
# Vector ops
# ============================================================
def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0; na = 0.0; nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _query_token_count(query_text: str) -> int:
    """Cheap content-token count for the query-richness gate."""
    if not query_text:
        return 0
    return sum(1 for tok in query_text.split() if len(tok) >= 3)


# ============================================================
# Era assignment per librarian
# ============================================================
def _compute_era_buckets(member_timestamps: List[str]) -> Dict[str, Tuple[str, str]]:
    """Given sorted member created_at strings, return era boundary timestamps.

    Returns {era: (lo_ts, hi_ts)} where lo_ts is inclusive, hi_ts exclusive,
    except 'recent' which is unbounded high.

    With ERA_QUANTILES = [0.33, 0.67]:
      early  = [min,  q33]
      middle = [q33,  q67]
      recent = [q67,  max]
    """
    if not member_timestamps:
        return {}
    ts = sorted(member_timestamps)
    n = len(ts)
    q1_idx = max(0, int(n * ERA_QUANTILES[0]) - 1)
    q2_idx = max(q1_idx + 1, int(n * ERA_QUANTILES[1]) - 1)
    q2_idx = min(q2_idx, n - 1)
    return {
        "early":  (ts[0],          ts[q1_idx]),
        "middle": (ts[q1_idx],     ts[q2_idx]),
        "recent": (ts[q2_idx],     ts[-1]),
    }


def _bucket_for_ts(ts: str, eras: Dict[str, Tuple[str, str]]) -> str:
    """Assign a timestamp to its era. ISO-8601 strings sort lexically."""
    if not eras:
        return "recent"
    if ts < eras["middle"][0]:
        return "early"
    if ts < eras["recent"][0]:
        return "middle"
    return "recent"


# ============================================================
# Compute era-aware sub-centroids per librarian
# ============================================================
def compute_era_subcentroids_for_librarian(
    librarian_id: int,
    min_members: int = 30,
    min_per_era: int = 8,
) -> Optional[Dict[str, Any]]:
    """For one librarian:
       1. Pull all (memory_id, embedding, created_at) tuples for members.
       2. Compute per-librarian era boundaries from the timestamp distribution.
       3. Bucket members by era.
       4. Within each era with >= min_per_era members, run KMeans with
          k = min(8, max(2, int(sqrt(n_era / 6)))).
       5. Persist as librarian_era_subcentroids rows.

    Returns summary dict or None if too small.
    """
    c = _open_db()
    try:
        member_count = c.execute(
            "SELECT COUNT(*) AS n FROM librarian_membership WHERE librarian_id = ?",
            (librarian_id,),
        ).fetchone()["n"]

        if member_count < min_members:
            return None

        rows = c.execute(
            """SELECT m.id AS memory_id,
                      e.embedding_json,
                      m.created_at
               FROM librarian_membership lm
               JOIN memories m ON m.id = lm.memory_id
               JOIN memory_embeddings e ON e.memory_id = m.id
               WHERE lm.librarian_id = ?
                 AND m.status != 'rejected'
                 AND m.created_at IS NOT NULL""",
            (librarian_id,),
        ).fetchall()

        if len(rows) < min_members:
            return None

        # Parse embeddings + timestamps
        records: List[Tuple[List[float], str]] = []
        for r in rows:
            try:
                v = json.loads(r["embedding_json"])
                if isinstance(v, list) and len(v) > 0:
                    records.append((v, r["created_at"]))
            except (json.JSONDecodeError, TypeError):
                continue

        if len(records) < min_members:
            return None

        # Era buckets
        eras = _compute_era_buckets([r[1] for r in records])
        bucketed: Dict[str, List[List[float]]] = {"early": [], "middle": [], "recent": []}
        for vec, ts in records:
            bucketed[_bucket_for_ts(ts, eras)].append(vec)

        # KMeans per era
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except ImportError as e:
            return {"error": f"sklearn unavailable: {e}"}

        # Clear prior era sub-centroids for this librarian
        c.execute(
            "DELETE FROM librarian_era_subcentroids WHERE librarian_id = ?",
            (librarian_id,),
        )

        now = _now()
        per_era_summary: Dict[str, Any] = {}
        any_built = False

        for era, vecs in bucketed.items():
            if len(vecs) < min_per_era:
                per_era_summary[era] = {"n": len(vecs), "k": 0, "skipped": "too_few"}
                continue

            n = len(vecs)
            k = min(8, max(2, int(math.sqrt(n / 6.0))))

            X = np.array(vecs, dtype=np.float32)
            km = KMeans(n_clusters=k, n_init=3, random_state=42, max_iter=100)
            labels = km.fit_predict(X)
            centroids = km.cluster_centers_
            sizes = [int((labels == i).sum()) for i in range(k)]

            era_lo = eras[era][0] if era in eras else None
            era_hi = eras[era][1] if era in eras else None

            for i in range(k):
                centroid_list = centroids[i].tolist()
                c.execute(
                    """INSERT INTO librarian_era_subcentroids
                       (librarian_id, era, cluster_id, centroid_embedding,
                        cluster_size, era_lo_ts, era_hi_ts, computed_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (librarian_id, era, i, json.dumps(centroid_list),
                     sizes[i], era_lo, era_hi, now),
                )
            per_era_summary[era] = {"n": n, "k": k, "cluster_sizes": sizes}
            any_built = True

        if not any_built:
            return {"librarian_id": librarian_id, "skipped": "no_era_met_min"}

        c.commit()

        return {
            "librarian_id": librarian_id,
            "n_used": len(records),
            "eras": per_era_summary,
            "computed_at": now,
        }
    finally:
        c.close()


def compute_all_era_subcentroids(
    min_members: int = 30,
    min_per_era: int = 8,
    force: bool = False,
) -> Dict[str, Any]:
    """Compute era-aware sub-centroids for every librarian with enough members."""
    init_schema()
    c = _open_db()
    try:
        rows = c.execute("""
            SELECT id, name, category, member_count
            FROM librarians
            WHERE category != 'meta' OR name = 'meta_identity_core'
            ORDER BY member_count DESC
        """).fetchall()
    finally:
        c.close()

    updated: List[Dict[str, Any]] = []
    skipped_too_small: List[Dict[str, Any]] = []
    skipped_existing: List[str] = []
    failed: List[Dict[str, Any]] = []

    for r in rows:
        lib_id = r["id"]
        name = r["name"]
        n_members = r["member_count"] or 0

        if n_members < min_members:
            skipped_too_small.append({"name": name, "members": n_members})
            continue

        if not force:
            c2 = _open_db()
            try:
                existing = c2.execute(
                    "SELECT COUNT(*) AS n FROM librarian_era_subcentroids WHERE librarian_id = ?",
                    (lib_id,),
                ).fetchone()["n"]
            finally:
                c2.close()
            if existing > 0:
                skipped_existing.append(name)
                continue

        try:
            result = compute_era_subcentroids_for_librarian(
                lib_id, min_members=min_members, min_per_era=min_per_era,
            )
            if result is None:
                skipped_too_small.append({"name": name, "members": n_members})
            elif "error" in result:
                failed.append({"name": name, "reason": result["error"]})
            elif "skipped" in result:
                skipped_too_small.append({"name": name, "reason": result["skipped"]})
            else:
                updated.append({
                    "name": name,
                    "n_used": result["n_used"],
                    "eras": result["eras"],
                })
        except Exception as e:
            failed.append({"name": name, "reason": str(e)})

    return {
        "updated_count": len(updated),
        "updated": updated,
        "skipped_too_small_count": len(skipped_too_small),
        "skipped_existing_count": len(skipped_existing),
        "failed": failed,
        "config": {
            "min_members": min_members,
            "min_per_era": min_per_era,
            "era_quantiles": ERA_QUANTILES,
        },
    }


# ============================================================
# Routing, gated era-aware sub-centroid + single centroid
# ============================================================
async def era_subcentroid_route(
    query_text: str,
    top_k: int = 5,
    min_similarity: float = 0.15,
) -> Tuple[List[Dict[str, Any]], float]:
    """For each librarian, compute single_centroid_sim and max sub-centroid
    sim across all eras. Gate the sub-centroid contribution by margin,
    density, and query richness.

    Returns:
        (ranked list of librarian-score dicts, top1_top2_margin)
    """
    try:
        import cama_mcp
        query_vec = await cama_mcp._get_embedding(query_text)
    except Exception as e:
        print(f"[Phase 2.6] embedding failed: {e}", file=sys.stderr)
        return [], 0.0

    if not query_vec:
        return [], 0.0

    query_richness = _query_token_count(query_text)
    query_rich_enough = query_richness >= GATE_QUERY_TOKEN_MIN

    c = _open_db()
    try:
        # Pull all librarian single-centroids
        single_rows = c.execute("""
            SELECT id, name, category, centroid_embedding
            FROM librarians
            WHERE centroid_embedding IS NOT NULL
        """).fetchall()

        # Pull all era sub-centroids
        sub_rows = c.execute("""
            SELECT s.librarian_id, s.era, s.cluster_id,
                   s.centroid_embedding, s.cluster_size
            FROM librarian_era_subcentroids s
        """).fetchall()

        # Per-librarian: best sub-centroid score + density + margin against
        # the second-best sub-centroid in the same librarian.
        per_lib_sub: Dict[int, Dict[str, Any]] = {}
        per_lib_all_sub: Dict[int, List[float]] = {}
        for r in sub_rows:
            try:
                centroid = json.loads(r["centroid_embedding"])
            except (json.JSONDecodeError, TypeError):
                continue
            sim = _cosine(query_vec, centroid)
            lib_id = r["librarian_id"]
            per_lib_all_sub.setdefault(lib_id, []).append(sim)

            cur_best = per_lib_sub.get(lib_id)
            if cur_best is None or sim > cur_best["sim"]:
                per_lib_sub[lib_id] = {
                    "sim": sim,
                    "era": r["era"],
                    "cluster_id": r["cluster_id"],
                    "cluster_size": r["cluster_size"],
                }

        # Build final scores
        scored: List[Dict[str, Any]] = []
        for r in single_rows:
            try:
                single_centroid = json.loads(r["centroid_embedding"])
            except (json.JSONDecodeError, TypeError):
                continue
            single_sim = _cosine(query_vec, single_centroid)
            if single_sim < min_similarity:
                continue

            lib_id = r["id"]
            sub_info = per_lib_sub.get(lib_id)
            sub_sim = sub_info["sim"] if sub_info else 0.0
            sub_era = sub_info["era"] if sub_info else None
            sub_cluster = sub_info["cluster_id"] if sub_info else None
            sub_density = sub_info["cluster_size"] if sub_info else 0

            # Gate evaluation
            all_sub = per_lib_all_sub.get(lib_id, [])
            if len(all_sub) >= 2:
                sorted_sub = sorted(all_sub, reverse=True)
                margin = sorted_sub[0] - sorted_sub[1]
            else:
                margin = 0.0

            gate_margin   = margin >= GATE_MARGIN_MIN
            gate_density  = sub_density >= GATE_DENSITY_MIN
            gate_query    = query_rich_enough
            gate_open     = bool(gate_margin and gate_density and gate_query)

            # Combined score
            if gate_open and sub_sim > 0:
                combined = single_sim + ALPHA_BOOST * (sub_sim - single_sim) \
                           if sub_sim > single_sim else single_sim
            else:
                combined = single_sim

            scored.append({
                "librarian_id": lib_id,
                "name": r["name"],
                "category": r["category"],
                "single_sim": single_sim,
                "sub_sim": sub_sim,
                "sub_era": sub_era,
                "sub_cluster": sub_cluster,
                "sub_density": sub_density,
                "margin": margin,
                "gate_open": gate_open,
                "combined": combined,
            })

        scored.sort(key=lambda d: d["combined"], reverse=True)

        # Top-1/top-2 margin across librarians (for confidence telemetry)
        if len(scored) >= 2:
            top_margin = scored[0]["combined"] - scored[1]["combined"]
        elif len(scored) == 1:
            top_margin = scored[0]["combined"]
        else:
            top_margin = 0.0

        return scored[:top_k], top_margin
    finally:
        c.close()


def blend_routing_v4(
    keyword_results: List[Dict[str, Any]],
    sub_results: List[Dict[str, Any]],
    embedding_weight: float = EMBEDDING_WEIGHT,
    max_librarians: int = 5,
) -> List[Dict[str, Any]]:
    """Blend keyword routing with the gated era-aware sub-centroid scores."""
    by_id: Dict[int, Dict[str, Any]] = {}

    for kw in keyword_results:
        lib_id = kw.get("librarian_id") or kw.get("id")
        by_id[lib_id] = {
            "librarian_id": lib_id,
            "name": kw["name"],
            "category": kw["category"],
            "keyword_score": kw["score"],
            "single_sim": 0.0,
            "sub_sim": 0.0,
            "sub_era": None,
            "sub_cluster": None,
            "gate_open": False,
            "combined": kw["score"],
            "blended_score": kw["score"],
            "sources": ["keyword"],
        }

    for sub in sub_results:
        lib_id = sub["librarian_id"]
        amplified = sub["combined"] * embedding_weight
        if lib_id in by_id:
            existing = by_id[lib_id]
            existing.update({
                "single_sim": sub["single_sim"],
                "sub_sim": sub["sub_sim"],
                "sub_era": sub["sub_era"],
                "sub_cluster": sub["sub_cluster"],
                "gate_open": sub["gate_open"],
                "combined": sub["combined"],
                "blended_score": max(existing["keyword_score"], amplified),
            })
            existing["sources"].append("era_subcentroid")
        else:
            by_id[lib_id] = {
                "librarian_id": lib_id,
                "name": sub["name"],
                "category": sub["category"],
                "keyword_score": 0.0,
                "single_sim": sub["single_sim"],
                "sub_sim": sub["sub_sim"],
                "sub_era": sub["sub_era"],
                "sub_cluster": sub["sub_cluster"],
                "gate_open": sub["gate_open"],
                "combined": sub["combined"],
                "blended_score": amplified,
                "sources": ["era_subcentroid"],
            }

    ranked = sorted(by_id.values(), key=lambda d: d["blended_score"], reverse=True)
    return ranked[:max_librarians]


# ============================================================
# FastMCP registration
# ============================================================
def register(mcp):
    """Register Phase 2.6 tools."""

    @mcp.tool(
        name="cama_lib_compute_era_subcentroids",
        annotations={
            "title": "Phase 2.6, Compute Era-Aware Sub-Centroids per Librarian",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_compute_era_subcentroids(
        force: bool = False,
        min_members: int = 30,
        min_per_era: int = 8,
    ) -> str:
        """Cluster each librarian's member embeddings into era-bucketed
        sub-centroids via KMeans. Per-librarian era boundaries are computed
        from the leaf's own created_at timestamp distribution.

        Args:
            force: if False, skip librarians that already have era sub-centroids
            min_members: skip librarians with fewer total members than this
            min_per_era: skip eras with fewer members than this
        """
        result = compute_all_era_subcentroids(
            min_members=min_members,
            min_per_era=min_per_era,
            force=force,
        )
        return json.dumps(result, indent=2, default=str)

    @mcp.tool(
        name="cama_lib_route_v4",
        annotations={
            "title": "Phase 2.6, Era-Aware Gated Hybrid Routing",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def cama_lib_route_v4(
        query_text: str,
        max_librarians: int = 5,
        per_librarian_limit: int = 8,
        overall_limit: int = 25,
        embedding_weight: float = EMBEDDING_WEIGHT,
        min_similarity: float = 0.15,
    ) -> str:
        """Route a query using keyword + era-aware gated sub-centroid scoring.

        Single-centroid sim is the stabilizer; sub-centroid sim acts as a
        gated boost. The era axis lets time-stratified leaves route to the
        right era's cluster.

        Args:
            query_text: the user's message
            max_librarians: how many leaves to activate
            per_librarian_limit: memories per leaf
            overall_limit: total memory cap
            embedding_weight: blend amplification for embedding-side score
            min_similarity: cosine threshold
        """
        try:
            from cama.librarian import cama_librarian
            keyword_libs = cama_librarian.route(
                query_text, max_librarians=max_librarians
            )
        except Exception as e:
            return json.dumps({"error": f"keyword route failed: {e}"})

        sub_results, top_margin = await era_subcentroid_route(
            query_text, top_k=max_librarians * 2, min_similarity=min_similarity
        )

        blended = blend_routing_v4(
            keyword_libs, sub_results,
            embedding_weight=embedding_weight,
            max_librarians=max_librarians,
        )

        # Fetch memories
        c = _open_db()
        try:
            memories = []
            seen_mids = set()
            for lib in blended:
                lib_id = lib["librarian_id"]
                if lib_id is None:
                    continue
                rows = c.execute(
                    """SELECT m.id AS memory_id, m.raw_text, m.memory_type,
                              m.context, m.is_core,
                              lm.membership_strength
                       FROM librarian_membership lm
                       JOIN memories m ON m.id = lm.memory_id
                       WHERE lm.librarian_id = ?
                         AND m.status != 'rejected'
                       ORDER BY m.is_core DESC, m.created_at DESC
                       LIMIT ?""",
                    (lib_id, per_librarian_limit),
                ).fetchall()
                for r in rows:
                    if r["memory_id"] in seen_mids:
                        continue
                    seen_mids.add(r["memory_id"])
                    memories.append({
                        "memory_id": r["memory_id"],
                        "raw_text": (r["raw_text"] or "")[:500],
                        "memory_type": r["memory_type"],
                        "context": r["context"],
                        "is_core": bool(r["is_core"]),
                        "from_librarian": lib["name"],
                        "from_era": lib.get("sub_era"),
                        "from_cluster": lib.get("sub_cluster"),
                        "membership_strength": r["membership_strength"],
                    })
            memories = memories[:overall_limit]

            return json.dumps({
                "query": query_text,
                "top_margin": top_margin,
                "librarians_activated": [
                    {
                        "name": lib["name"],
                        "category": lib["category"],
                        "keyword_score": lib["keyword_score"],
                        "single_sim": lib["single_sim"],
                        "sub_sim": lib["sub_sim"],
                        "sub_era": lib["sub_era"],
                        "sub_cluster": lib["sub_cluster"],
                        "gate_open": lib["gate_open"],
                        "combined": lib["combined"],
                        "blended_score": lib["blended_score"],
                        "sources": lib["sources"],
                    }
                    for lib in blended
                ],
                "memories": memories,
                "config": {
                    "alpha_boost": ALPHA_BOOST,
                    "gate_margin_min": GATE_MARGIN_MIN,
                    "gate_density_min": GATE_DENSITY_MIN,
                    "gate_query_token_min": GATE_QUERY_TOKEN_MIN,
                    "era_quantiles": ERA_QUANTILES,
                    "embedding_weight": embedding_weight,
                },
            }, indent=2, default=str)
        finally:
            c.close()

    print(
        "[CAMA] Phase 2.6 era-aware gated hybrid loaded, "
        "cama_lib_compute_era_subcentroids, cama_lib_route_v4",
        file=__import__('sys').stderr,
        flush=True,
    )


# Initialize schema on import
init_schema()
