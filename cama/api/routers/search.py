"""``POST /v1/search``, Phase-1 librarian routing + counterweight injection.

Pipeline (RETRIEVAL.md § 2):

  1. Score each librarian's ``routing_keywords`` against the query
     (substring match × ``activation_score``). Top-5 wins.
  2. Dyad-scoped JOIN against ``librarian_membership``, only memories
     belonging to an activated librarian *and* to the calling key's
     dyad surface.
  3. Falls back to ``LOWER(raw_text) LIKE ?`` when the routing index
     produced nothing (fresh dyad / librarians not populated yet).
     ``routing.phase`` in the response reflects which path actually
     ran (``"1"`` vs ``"1_keyword_fallback"``).
  4. Counterweight injection runs on top of either path when the
     query's affect crosses the negative-affect predicate. De-duped
     against the regular result set so a routed memory doesn't
     double-count.

Phase-2.6 era-aware semantic routing is NOT wired here yet. It lives
in ``cama/librarian/cama_phase26_era_hybrid.py`` and depends on the
sentence-transformer cache being portable across the API and MCP
processes. Tracked as v1.2 work in API.md § 13.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends

from cama.api.auth import AuthContext
from cama.api.deps import (
    is_negative_affect,
    open_memory_db,
    require_auth,
)
from cama.api.schemas import (
    RoutingMeta,
    ScoreBreakdown,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)

router = APIRouter(tags=["search"])

LIBRARIAN_MAX_ACTIVATED = 5


def _librarian_routed_search(
    c: sqlite3.Connection,
    dyad_id: str,
    query: str,
    statuses: tuple[str, ...],
    limit: int,
) -> tuple[list[sqlite3.Row], int]:
    """Phase-1 librarian-routed retrieval (RETRIEVAL.md § 2).

    Routes the query against ``librarians.routing_keywords`` (in-process
    keyword scoring, same algorithm as
    ``cama.librarian.cama_librarian.route()``), then issues a
    dyad-scoped JOIN against ``librarian_membership`` so only memories
    that belong to one of the activated librarians AND to this dyad
    surface.

    Why this re-implements the routing inline instead of calling the
    librarian module: the librarian module captures ``DB_PATH`` at
    module-import time, but the API server's per-test fixtures swap
    ``CAMA_DB_PATH`` between tests via monkeypatch. Doing the routing
    against the caller's already-open connection means the routing
    index and the membership join always read from the same DB the
    rest of the search query reads from, no path drift, no separate
    connection lifecycle to manage.

    Returns ``(rows, librarians_activated_count)``. Rows are ordered
    by ``MAX(membership_strength)`` descending, with ``created_at``
    as the tie-breaker.

    Returns ``([], 0)`` if:
      * The librarians table doesn't exist on this DB yet, OR
      * No librarian's routing_keywords matched the query, OR
      * The dyad has no memberships in any matched librarian.
    The caller treats any of those as a signal to fall back to the
    keyword LIKE path, so the contract doesn't degrade on fresh dyads
    or fresh installs.
    """
    # 1. Pull all non-meta librarians' keyword rows from the same
    #    connection we'll later join against, avoids the DB_PATH
    #    drift problem (see docstring).
    try:
        lib_rows = c.execute(
            "SELECT id, routing_keywords, activation_score "
            "FROM librarians "
            "WHERE category != 'meta' OR name = 'meta_identity_core'"
        ).fetchall()
    except sqlite3.OperationalError:
        # librarians table doesn't exist; fall back to keyword LIKE.
        return [], 0

    # 2. Score each librarian against the query's lowercased substring
    #    membership over routing_keywords. This matches the algorithm
    #    in cama.librarian.cama_librarian.route() so the API and the
    #    MCP tool surface the same activation set for the same query.
    q_lower = query.lower()
    candidates: list[tuple[int, float]] = []
    for r in lib_rows:
        if not r["routing_keywords"]:
            continue
        try:
            keywords = json.loads(r["routing_keywords"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(keywords, list):
            continue
        score = 0.0
        for kw in keywords:
            if kw and isinstance(kw, str) and kw.lower() in q_lower:
                score += 1.0
        if score > 0:
            score *= float(r["activation_score"] or 1.0)
            candidates.append((int(r["id"]), score))

    if not candidates:
        return [], 0
    candidates.sort(key=lambda t: t[1], reverse=True)
    candidates = candidates[:LIBRARIAN_MAX_ACTIVATED]
    lib_ids = [t[0] for t in candidates]

    # 3. Dyad-scoped JOIN. The lm.librarian_id IN (...) clause limits
    #    the fan-out to activated librarians; m.dyad_id = ? enforces
    #    tenant isolation; m.status IN (...) honors the
    #    include_provisional flag.
    lib_placeholders = ",".join(["?"] * len(lib_ids))
    status_placeholders = ",".join(["?"] * len(statuses))
    sql = (
        "SELECT m.*, MAX(lm.membership_strength) AS membership_strength "
        "FROM memories m "
        "JOIN librarian_membership lm ON m.id = lm.memory_id "
        f"WHERE lm.librarian_id IN ({lib_placeholders}) "
        "AND m.dyad_id = ? "
        f"AND m.status IN ({status_placeholders}) "
        "GROUP BY m.id "
        "ORDER BY membership_strength DESC, m.created_at DESC "
        "LIMIT ?"
    )
    params: list[Any] = lib_ids + [dyad_id] + list(statuses) + [limit]
    try:
        rows = c.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # librarian_membership table missing, fresh install.
        return [], 0
    return rows, len(lib_ids)


@router.post("/v1/search", response_model=SearchResponse)
def search(
    payload: SearchRequest,
    ctx: AuthContext = Depends(require_auth),
) -> SearchResponse:
    t0 = time.perf_counter()
    query = payload.query
    limit = payload.limit
    # Whether the query qualifies for counterweight injection:
    is_negative_query = is_negative_affect(payload.affect)

    statuses: tuple[str, ...] = ("durable",)
    if payload.include_provisional:
        statuses = ("durable", "provisional")

    c = open_memory_db()
    try:
        # ── Path A: Phase-1 librarian-routed retrieval ────────────────────
        # This is the real CAMA retrieval pipeline (RETRIEVAL.md § 2):
        # route() picks 1-5 librarians whose routing_keywords match the
        # query, then a dyad-scoped JOIN against librarian_membership
        # surfaces only memories that belong to one of those librarians
        # AND to the caller's dyad. We try this first; if it produces
        # no rows (dyad not yet populated, or routing missed) we fall
        # back to Path B so the contract doesn't degrade.
        lib_rows, librarians_activated = _librarian_routed_search(
            c, ctx.dyad_id, query, statuses, limit
        )
        phase: str
        results: list[SearchResultItem] = []
        seen_ids: set[int] = set()
        if lib_rows:
            phase = "1"
            for r in lib_rows:
                seen_ids.add(r["id"])
                # membership_strength is in [0, 1]; map it directly to
                # the surfaced score. Full blended scoring (semantic +
                # affect + relational + recency) lands in v1.2 once
                # the embedding cache is portable across the API and
                # MCP processes (RETRIEVAL.md § 5).
                strength = float(r["membership_strength"] or 0.5)
                results.append(
                    SearchResultItem(
                        id=r["id"],
                        text=(r["raw_text"] or "")[:1000],
                        memory_type=r["memory_type"],
                        proposed_by=r["proposed_by"],
                        source_type=r["source_type"],
                        score=round(strength, 3),
                        score_breakdown=ScoreBreakdown(
                            semantic=0.0,
                            affect=0.0,
                            relational=round(strength, 3),
                            recency=0.0,
                        ),
                        is_counterweight=False,
                        created_at=r["created_at"],
                    )
                )
        else:
            # ── Path B: keyword LIKE fallback ─────────────────────────────
            # Used when librarian routing produces nothing (empty
            # routing index, or a fresh dyad with no memberships yet).
            # Preserves the v1 behavior so a dyad can still search
            # before populate has been run.
            phase = "1_keyword_fallback"
            words = [w for w in query.lower().strip().split() if len(w) >= 2]
            if not words:
                words = [query.strip()]
            like_clauses = " OR ".join(["LOWER(raw_text) LIKE ?"] * len(words))
            params: list[Any] = (
                [f"%{w}%" for w in words] + list(statuses) + [ctx.dyad_id]
            )
            sql = (
                "SELECT * FROM memories "
                f"WHERE ({like_clauses}) AND status IN "
                f"({','.join(['?'] * len(statuses))}) "
                "AND dyad_id = ? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = c.execute(sql, params).fetchall()
            for r in rows:
                seen_ids.add(r["id"])
                results.append(
                    SearchResultItem(
                        id=r["id"],
                        text=(r["raw_text"] or "")[:1000],
                        memory_type=r["memory_type"],
                        proposed_by=r["proposed_by"],
                        source_type=r["source_type"],
                        score=0.5,
                        score_breakdown=ScoreBreakdown(
                            semantic=0.0,
                            affect=0.0,
                            relational=0.0,
                            recency=1.0,
                        ),
                        is_counterweight=False,
                        created_at=r["created_at"],
                    )
                )

        # Counterweight injection: on by default for negative-affect
        # queries. The dyad-level opt-out lives in the dyad's consent
        # record, which we treat as enabled in v1 single-tenant mode.
        # The opt-out path is documented in API.md § 4.5 and surfaces
        # in the response warnings.
        injected_count = 0
        warnings: list[str] = []
        if is_negative_query and len(results) > 0:
            cw_rows = c.execute(
                "SELECT * FROM memories "
                "WHERE counterweight_type IS NOT NULL "
                "AND status = 'durable' "
                "AND dyad_id = ? "
                "ORDER BY RANDOM() LIMIT 3",
                (ctx.dyad_id,),
            ).fetchall()
            for r in cw_rows:
                # Don't double-count a counterweight that already
                # surfaced as a regular hit, the SDK consumes
                # is_counterweight as a clear signal, so de-dup matters.
                if r["id"] in seen_ids:
                    continue
                seen_ids.add(r["id"])
                results.append(
                    SearchResultItem(
                        id=r["id"],
                        text=(r["raw_text"] or "")[:1000],
                        memory_type=r["memory_type"],
                        proposed_by=r["proposed_by"],
                        source_type=r["source_type"],
                        score=0.35,
                        score_breakdown=ScoreBreakdown(
                            semantic=0.0,
                            affect=0.7,
                            relational=0.0,
                            recency=0.5,
                        ),
                        is_counterweight=True,
                        created_at=r["created_at"],
                    )
                )
                injected_count += 1
    finally:
        c.close()

    latency_ms = (time.perf_counter() - t0) * 1000.0
    return SearchResponse(
        results=results,
        routing=RoutingMeta(
            phase=phase,
            librarians_activated=librarians_activated,
            counterweights_injected=injected_count,
            latency_ms=round(latency_ms, 2),
        ),
        warnings=warnings,
    )
