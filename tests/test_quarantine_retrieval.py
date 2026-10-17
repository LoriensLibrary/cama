"""Quarantine is an eligibility boundary, not merely a low ranking score."""
import asyncio
import json

import pytest

from mcp_sections import retrieval


@pytest.fixture
def isolated(fresh_db, monkeypatch):
    c, m = fresh_db
    monkeypatch.setattr(retrieval, "_session_tick", lambda: None)
    monkeypatch.setattr(retrieval, "_buf_track", lambda *a: None)
    monkeypatch.setattr(retrieval, "_buf_flush_if_ready", lambda: None)

    async def vector(*a):
        return [1.0, 0.0]

    monkeypatch.setattr(retrieval, "_get_embedding", vector)
    return c, m


def add(c, status, core=0):
    mid = c.execute(
        "INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,"
        "consent_level,is_core,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("orchard evidence", "exchange", "exchange", status, "system", "low", core,
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    ).lastrowid
    c.commit()
    return mid


@pytest.mark.parametrize("core", [0, 1])
def test_query_excludes_quarantine_even_from_semantic_pool(isolated, monkeypatch, core):
    c, m = isolated
    blocked = add(c, "quarantined", core)
    allowed = add(c, "durable")
    monkeypatch.setattr(retrieval._emb_store, "top_k_semantic", lambda *a, **k: [(blocked, 1.0), (allowed, 0.5)])
    monkeypatch.setattr(retrieval._emb_store, "sims_for", lambda *a: {blocked: 1.0, allowed: 0.5})
    result = json.loads(asyncio.run(retrieval.cama_query_memories(m.QueryInput(
        query_text="orchard", top_k=20, include_counterweight=False))))
    assert [r["id"] for r in result["results"]] == [allowed]
    assert c.execute("SELECT COUNT(*) FROM ring WHERE memory_id=?", (blocked,)).fetchone()[0] == 0
    assert c.execute("SELECT access_count FROM memories WHERE id=?", (blocked,)).fetchone()[0] == 0


def test_all_quarantined_query_returns_empty(isolated):
    c, m = isolated
    add(c, "quarantined", 1)
    result = json.loads(asyncio.run(retrieval.cama_query_memories(m.QueryInput(
        query_text="orchard", filters={"status": "quarantined"}, include_counterweight=False))))
    assert result["results"] == []


@pytest.mark.parametrize("include_provisional", [False, True])
def test_keyword_search_does_not_treat_quarantine_as_provisional(isolated, include_provisional):
    c, _ = isolated
    add(c, "quarantined", 1)
    durable = add(c, "durable")
    provisional = add(c, "provisional")
    result = json.loads(asyncio.run(retrieval.cama_search("orchard", include_provisional=include_provisional)))
    expected = {durable, provisional} if include_provisional else {durable}
    assert {r["id"] for r in result["results"]} == expected
