"""retrieval_feedback gets a writer (review finding K-3). Temp stores only."""
import asyncio
import json

import pytest

from cama.core import retrieval_feedback as fb
from mcp_sections import retrieval, safety


@pytest.fixture
def isolated(fresh_db, monkeypatch):
    c, m = fresh_db
    monkeypatch.setattr(retrieval, "_session_tick", lambda: None)
    monkeypatch.setattr(retrieval, "_buf_track", lambda *a: None)
    monkeypatch.setattr(retrieval, "_buf_flush_if_ready", lambda: None)

    async def vector(*a):
        return []

    monkeypatch.setattr(retrieval, "_get_embedding", vector)
    return c, m


def add(c, text="orchard evidence"):
    mid = c.execute(
        "INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,"
        "consent_level,is_core,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (text, "exchange", "exchange", "durable", "system", "low", 0,
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    ).lastrowid
    c.commit()
    return mid


def test_query_records_every_returned_row_as_shown(isolated):
    c, m = isolated
    a = add(c, "orchard evidence one")
    b = add(c, "orchard evidence two")
    add(c, "unrelated")
    out = json.loads(asyncio.run(retrieval.cama_query_memories(m.QueryInput(
        query_text="orchard", top_k=5, include_counterweight=False))))
    returned = [r["id"] for r in out["results"]]
    assert {a, b} <= set(returned)  # no embeddings here: the structural pool returns every row
    assert out["feedback_recorded"] == len(returned)
    rows = c.execute("SELECT memory_id, query_text, feedback, weight_delta FROM retrieval_feedback ORDER BY id").fetchall()
    assert sorted(r["memory_id"] for r in rows) == sorted(returned)
    assert all(r["feedback"] == "shown" and r["query_text"] == "orchard" and r["weight_delta"] == 0.0 for r in rows)


def test_mark_used_records_only_what_is_reported(isolated):
    c, m = isolated
    a = add(c)
    out = json.loads(asyncio.run(retrieval.cama_mark_used([a, a, 0, "x"], used=True, note="shaped the answer")))
    assert out == {"recorded": 1, "feedback": "used", "ignored": 3}
    out = json.loads(asyncio.run(retrieval.cama_mark_used([a], used=False)))
    assert out["feedback"] == "unhelpful" and out["recorded"] == 1
    kinds = [r[0] for r in c.execute("SELECT feedback FROM retrieval_feedback ORDER BY id")]
    assert kinds == ["used", "unhelpful"]
    # nothing else moved
    row = c.execute("SELECT status, access_count FROM memories WHERE id=?", (a,)).fetchone()
    assert row["status"] == "durable" and row["access_count"] == 0


def test_summary_links_shown_to_later_used_and_ignores_fixtures(isolated):
    c, m = isolated
    a = add(c, "orchard one")
    b = add(c, "orchard two")
    asyncio.run(retrieval.cama_query_memories(m.QueryInput(query_text="orchard", top_k=5, include_counterweight=False)))
    asyncio.run(retrieval.cama_mark_used([a]))
    fb.record(c, [b], "shown", "__benchmark_baseline__")
    c.commit()
    s = fb.summary(c, days=7)
    assert s["shown"] == 2 and s["used"] == 1 and s["unhelpful"] == 0
    assert s["distinct_shown"] == 2 and s["distinct_shown_then_used"] == 1
    c.execute("CREATE TABLE IF NOT EXISTS sleep_log (id INTEGER PRIMARY KEY, cycle_end TEXT, actions_taken TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS librarian_membership (librarian_id INTEGER, memory_id INTEGER)")
    c.commit()
    health = json.loads(asyncio.run(safety.cama_health()))
    assert health["retrieval_feedback_7d"]["shown"] == 2


def test_writer_creates_the_table_on_a_store_that_lacks_it(isolated):
    c, m = isolated
    c.execute("DROP TABLE IF EXISTS retrieval_feedback")
    c.commit()
    assert fb.record(c, [1, 2], "shown", "q") == 2
    c.commit()
    assert c.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0] == 2


def test_bad_kind_is_refused():
    with pytest.raises(ValueError):
        fb.record(None, [1], "liked")
