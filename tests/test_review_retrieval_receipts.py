"""Synthetic review receipts; strict xfails pin unresolved review findings."""
import asyncio
import json

import pytest

from cama.sleep import cama_consolidate
from mcp_sections import retrieval


@pytest.fixture
def isolated(fresh_db, monkeypatch):
    c, m = fresh_db
    monkeypatch.setattr(retrieval, "_session_tick", lambda: None)
    monkeypatch.setattr(retrieval, "_buf_track", lambda *a: None)
    monkeypatch.setattr(retrieval, "_buf_flush_if_ready", lambda: None)
    return c, m


def add(c, sensitive=False):
    mid = c.execute("INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,consent_level,is_core,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("synthetic orchard", "exchange", "exchange", "durable", "system", "high" if sensitive else "low", 1,
                     "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")).lastrowid
    c.commit()
    return mid


@pytest.mark.xfail(strict=True, reason="C-1: default keyword search exposes sensitive non-self-account rows")
def test_default_search_respects_consent(isolated):
    c, _ = isolated
    add(c, sensitive=True)
    assert json.loads(asyncio.run(retrieval.cama_search("orchard")))["results"] == []


@pytest.mark.xfail(strict=True, reason="C-1: core read has no sensitivity gate")
def test_default_core_respects_consent(isolated):
    c, _ = isolated
    add(c, sensitive=True)
    assert json.loads(asyncio.run(retrieval.cama_get_core()))["core"] == []


@pytest.mark.xfail(strict=True, reason="C-2: consolidation apply does not revalidate changed consent")
def test_stale_consolidation_report_cannot_copy_sensitive_text(isolated, monkeypatch):
    c, m = isolated
    mid = add(c)
    report = {"generated_at": "synthetic", "clusters": [
        {"exemplar_id": mid, "member_ids": [mid], "size": 1}]}
    c.execute("UPDATE memories SET consent_level='high' WHERE id=?", (mid,))
    c.commit()
    monkeypatch.setattr(cama_consolidate, "DB_PATH", m.DB_PATH)
    cama_consolidate.apply_report(report)
    assert c.execute("SELECT COUNT(*) FROM memories WHERE memory_type='schema'").fetchone()[0] == 0


@pytest.mark.xfail(strict=True, reason="C-3: a ring write failure aborts otherwise valid retrieval")
def test_ring_failure_does_not_discard_results(isolated, monkeypatch):
    c, m = isolated
    mid = add(c)

    async def no_embedding(*args):
        return []

    def failed_ring(*args):
        raise OSError("synthetic ring failure")

    monkeypatch.setattr(retrieval, "_get_embedding", no_embedding)
    monkeypatch.setattr(retrieval, "_ring_push", failed_ring)
    result = json.loads(asyncio.run(retrieval.cama_query_memories(m.QueryInput(
        query_text="orchard", include_counterweight=False))))
    assert result["results"][0]["id"] == mid
