"""Quarantine is an eligibility boundary, not merely a low ranking score."""
import asyncio
import json

import pytest

from mcp_sections import memory_lifecycle, retrieval, safety


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


def test_review_queue_pages_without_releasing_or_recalling(isolated):
    c, _ = isolated
    first = add(c, "quarantined")
    add(c, "durable")
    second = add(c, "quarantined", 1)
    add(c, "provisional")
    page = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined(limit=1)))
    assert page["review_only"] is True
    assert page["total_quarantined"] == 2
    assert [r["id"] for r in page["results"]] == [first]
    assert page["results"][0]["proposed_by"] == "system"
    assert page["results"][0]["raw_text"] == "orchard evidence"
    assert "trust_reason" in page["results"][0]
    page2 = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined(limit=1, after_id=page["next_after_id"])))
    assert [r["id"] for r in page2["results"]] == [second]
    assert page2["next_after_id"] is None
    assert c.execute("SELECT COUNT(*) FROM ring").fetchone()[0] == 0
    assert c.execute("SELECT SUM(access_count) FROM memories").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM memories WHERE status='quarantined'").fetchone()[0] == 2
    c.execute("CREATE TABLE IF NOT EXISTS sleep_log (id INTEGER PRIMARY KEY, cycle_end TEXT, actions_taken TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS librarian_membership (librarian_id INTEGER, memory_id INTEGER)")
    c.commit()
    health = json.loads(asyncio.run(safety.cama_health()))
    assert "error" not in health, health
    assert health["quarantined_pending"] == 2


@pytest.mark.parametrize("limit,after_id", [(0, 0), (101, 0), (1, -1)])
def test_review_queue_rejects_invalid_page(isolated, limit, after_id):
    assert "error" in json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined(limit, after_id)))


def test_empty_review_queue(isolated):
    result = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined()))
    assert result["results"] == []
    assert result["total_quarantined"] == 0
    assert result["next_after_id"] is None


def test_review_queue_requires_sensitive_opt_in_and_bounds_previews(isolated):
    c, _ = isolated
    secret = add(c, "quarantined")
    ordinary = add(c, "quarantined")
    c.execute("UPDATE memories SET consent_level='high',raw_text=?,context=? WHERE id=?",
              ("s" * 4000, "c" * 2000, secret))
    c.commit()
    default = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined()))
    assert [r["id"] for r in default["results"]] == [ordinary]
    assert default["total_quarantined"] == 1
    assert default["next_after_id"] is None
    preview = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined(include_sensitive=True)))
    row = preview["results"][0]
    assert row["id"] == secret and row["consent_level"] == "high"
    assert row["raw_text"] == "s" * 1000 and row["raw_text_chars"] == 4000
    assert row["raw_text_truncated"] and row["context_truncated"]
    full = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined(include_sensitive=True, include_full_text=True)))
    assert full["results"][0]["raw_text"] == "s" * 4000
    assert not full["results"][0]["raw_text_truncated"]
    without_consent = json.loads(asyncio.run(memory_lifecycle.cama_list_quarantined(include_full_text=True)))
    assert [r["id"] for r in without_consent["results"]] == [ordinary]
