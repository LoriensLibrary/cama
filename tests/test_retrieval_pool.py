"""Blended retrieval must be able to reach any memory in the store by meaning.

Before 2026-09-07 the retriever shortlisted 500 candidates ordered by
is_core before scoring. With more than 500 core memories the shortlist was
always core, so a non-core memory could never enter the pool no matter how
well it matched the query. These tests build exactly that situation, more
core rows than the old shortlist, and check that the one non-core memory
which actually matches the query comes back first.
"""

import asyncio
import json

import numpy as np
import pytest

from cama.core import embedding_store as es
from mcp_sections import retrieval

DIM = 8
QUERY = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def db(fresh_db, monkeypatch):
    conn, cama_mcp = fresh_db
    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "none")

    async def fixed_embedding(text):
        return list(QUERY) if text else []

    # retrieval binds _get_embedding by name at import, so patch it there.
    monkeypatch.setattr(retrieval, "_get_embedding", fixed_embedding)
    es.invalidate_matrix_cache()
    return conn, cama_mcp


def _seed(conn, cama_mcp, text, vec, core=0, status="durable", consent="low"):
    now = cama_mcp._now()
    cur = conn.execute(
        "INSERT INTO memories (raw_text, memory_type, source_type, status, proposed_by, "
        "consent_level, is_core, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (text, "experience", "teaching", status, "user", consent, core, now, now))
    mid = cur.lastrowid
    es.store_embedding(conn, mid, vec, "test", now)
    return mid


def _orthogonal(i):
    """A unit vector with no component along the query axis."""
    v = np.zeros(DIM, dtype=np.float32)
    v[1 + (i % (DIM - 1))] = 1.0
    return v.tolist()


def _query(cama_mcp, **kw):
    params = cama_mcp.QueryInput(query_text="the needle", top_k=5, include_counterweight=False, **kw)
    return json.loads(asyncio.run(retrieval.cama_query_memories(params)))


def test_non_core_needle_beats_six_hundred_core_haystack(db):
    conn, cama_mcp = db
    for i in range(600):
        _seed(conn, cama_mcp, f"core memory {i}", _orthogonal(i), core=1)
    needle = _seed(conn, cama_mcp, "the one that actually matches", QUERY, core=0)
    conn.commit()

    out = _query(cama_mcp)
    ids = [m["id"] for m in out["results"]]
    assert needle in ids, "a non-core memory matching the query must be reachable"
    assert ids[0] == needle, f"the exact match should rank first, got {ids[:5]}"
    assert out["pool"]["semantic"] >= 1
    assert out["used_embeddings"] is True


def test_semantic_pool_respects_status_and_consent(db):
    conn, cama_mcp = db
    for i in range(20):
        _seed(conn, cama_mcp, f"filler {i}", _orthogonal(i), core=1)
    rejected = _seed(conn, cama_mcp, "matches but rejected", QUERY, status="rejected")
    expired = _seed(conn, cama_mcp, "matches but expired", QUERY, status="expired")
    secret = _seed(conn, cama_mcp, "matches but high consent", QUERY, consent="high")
    conn.commit()

    ids = {m["id"] for m in _query(cama_mcp)["results"]}
    assert rejected not in ids
    assert expired not in ids
    assert secret not in ids

    ids = {m["id"] for m in _query(cama_mcp, filters={"include_sensitive": "true"})["results"]}
    assert secret in ids


def test_semantic_pool_respects_type_filters(db):
    conn, cama_mcp = db
    match = _seed(conn, cama_mcp, "matching experience", QUERY)
    conn.execute("UPDATE memories SET memory_type='experience' WHERE id=?", (match,))
    conn.commit()
    ids = {m["id"] for m in _query(cama_mcp, filters={"memory_type": "journal"})["results"]}
    assert match not in ids


def test_no_query_text_still_returns_recent_non_core_rows(db):
    conn, cama_mcp = db
    for i in range(300):
        _seed(conn, cama_mcp, f"core {i}", _orthogonal(i), core=1)
    recent = _seed(conn, cama_mcp, "newest non-core", _orthogonal(0), core=0)
    conn.commit()
    params = cama_mcp.QueryInput(query_text=None, top_k=20, include_counterweight=False)
    out = json.loads(asyncio.run(retrieval.cama_query_memories(params)))
    assert out["used_embeddings"] is False
    assert out["pool"]["recent"] >= 1
    assert any(m["id"] == recent for m in out["results"]) or out["pool"]["recent"] == 250


def test_matrix_cache_refreshes_after_a_new_embedding(db):
    conn, cama_mcp = db
    _seed(conn, cama_mcp, "old", _orthogonal(0), core=1)
    conn.commit()
    first = es.top_k_semantic(conn, QUERY, k=5)
    assert all(abs(sim) < 1e-6 for _, sim in first)

    needle = _seed(conn, cama_mcp, "new", QUERY)
    conn.commit()
    second = es.top_k_semantic(conn, QUERY, k=5)
    assert second and second[0][0] == needle
    assert second[0][1] == pytest.approx(1.0)


def test_mismatched_dimension_vectors_are_skipped(db):
    conn, cama_mcp = db
    good = _seed(conn, cama_mcp, "good", QUERY)
    _seed(conn, cama_mcp, "bad dim", [1.0, 0.0, 0.0])
    conn.commit()
    top = es.top_k_semantic(conn, QUERY, k=5)
    assert [mid for mid, _ in top] == [good]


def test_empty_store_and_empty_query_are_safe(db):
    conn, cama_mcp = db
    assert es.top_k_semantic(conn, QUERY, k=5) == []
    assert es.top_k_semantic(conn, [], k=5) == []
    assert es.top_k_semantic(conn, [0.0] * DIM, k=5) == []
    assert es.sims_for(conn, QUERY, [1, 2, 3]) == {}


def test_meaning_beats_structure_at_realistic_margins(db):
    """A well-connected core row with a weaker match must not outrank a
    non-core memory that matches better. Real cosines sit between 0.35 and
    0.65; here the core haystack scores 0.52 and the needle 0.63, both
    created now so recency cannot decide it. Under the old weights
    (relational 0.15, core x1.3) the haystack won by about 0.10."""
    import math

    conn, cama_mcp = db
    hay = [0.52, math.sqrt(1 - 0.52 ** 2)] + [0.0] * (DIM - 2)
    needle_vec = [0.63, math.sqrt(1 - 0.63 ** 2)] + [0.0] * (DIM - 2)
    for i in range(50):
        mid = _seed(conn, cama_mcp, f"connected core {i}", hay, core=1)
        conn.execute("UPDATE memories SET rel_degree=12 WHERE id=?", (mid,))
    needle = _seed(conn, cama_mcp, "the better match, no edges yet", needle_vec, core=0)
    conn.commit()

    ids = [m["id"] for m in _query(cama_mcp)["results"]]
    assert ids[0] == needle, f"meaning must carry the ranking; got {ids[:3]}"


def test_auto_recorded_activity_never_competes(db):
    """Session-activity telemetry carries the literal query text, so with the
    substring fallback it matched itself at 0.6 and came back first."""
    conn, cama_mcp = db
    real = _seed(conn, cama_mcp, "a real memory", QUERY)
    now = cama_mcp._now()
    conn.execute(
        "INSERT INTO memories (raw_text, memory_type, context, source_type, status, proposed_by, "
        "is_core, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("[AUTO-RECORDED SESSION ACTIVITY] Tools: query. Context: the needle",
         "exchange", "auto-recorded", "exchange", "durable", "system", 0, now, now))
    conn.commit()
    ids = [m["id"] for m in _query(cama_mcp)["results"]]
    assert real in ids
    assert all(not m["raw_text"].startswith("[AUTO-RECORDED") for m in _query(cama_mcp)["results"])
