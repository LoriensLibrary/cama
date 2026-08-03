"""Schematization (consolidation pass) tests.

Pins the v1 consolidation contract from cama/sleep/cama_consolidate.py:
near-duplicate clusters collapse into one schema node with provenance
edges; members are demoted from core but never deleted or edited; and
the untouchable classes (teachings, counterweights, high-consent) are
never clustered.
"""

import json

import numpy as np

from cama.core import embedding_store as emb_store
from cama.sleep import cama_consolidate as consol

TS = "2026-08-03T00:00:00Z"


def _insert(conn, text, vec, *, source_type="inference", is_core=1,
            counterweight_type=None, consent_level="low"):
    cur = conn.execute(
        "INSERT INTO memories (raw_text, memory_type, source_type, status, "
        "proposed_by, evidence, confidence, is_core, counterweight_type, "
        "consent_level, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (text, "experience", source_type, "durable", "assistant",
         "[]", 1.0, is_core, counterweight_type, consent_level, TS, TS),
    )
    mid = cur.lastrowid
    emb_store.store_embedding(conn, mid, vec, "test-model", TS)
    conn.commit()
    return mid


def _seed(conn):
    """Two tight clusters + one outlier + untouchables near cluster A."""
    rng = np.random.default_rng(42)
    base_a = rng.normal(size=8)
    base_b = rng.normal(size=8)
    ids = {"a": [], "b": []}
    for i in range(4):
        ids["a"].append(_insert(conn, f"cluster A dup {i}",
                                base_a + rng.normal(scale=0.01, size=8)))
    for i in range(3):
        ids["b"].append(_insert(conn, f"cluster B dup {i}",
                                base_b + rng.normal(scale=0.01, size=8)))
    outlier = _insert(conn, "unique memory", rng.normal(size=8))
    teaching = _insert(conn, "teaching near A", base_a, source_type="teaching")
    counter = _insert(conn, "counterweight near A", base_a,
                      counterweight_type="anchor")
    sensitive = _insert(conn, "sensitive near A", base_a, consent_level="high")
    return ids, outlier, (teaching, counter, sensitive)


def test_analyze_finds_clusters_and_skips_untouchables(fresh_db, monkeypatch):
    conn, cama_mcp = fresh_db
    monkeypatch.setattr(consol, "DB_PATH", cama_mcp.DB_PATH)
    ids, outlier, untouchables = _seed(conn)

    report = consol.analyze("core", threshold=0.92, min_size=3)

    assert report["clusters_found"] == 2
    clustered = {m for cl in report["clusters"] for m in cl["member_ids"]}
    assert set(ids["a"]).issubset(clustered)
    assert set(ids["b"]).issubset(clustered)
    assert outlier not in clustered
    for mid in untouchables:
        assert mid not in clustered, "untouchable memory entered a cluster"


def test_apply_creates_schema_nodes_and_demotes(fresh_db, monkeypatch):
    conn, cama_mcp = fresh_db
    monkeypatch.setattr(consol, "DB_PATH", cama_mcp.DB_PATH)
    ids, _, _ = _seed(conn)

    report = consol.analyze("core", threshold=0.92, min_size=3)
    result = consol.apply_report(report)

    assert result["schema_nodes_created"] == 2
    assert result["members_demoted"] == 7

    schemas = conn.execute(
        "SELECT * FROM memories WHERE memory_type='schema'"
    ).fetchall()
    assert len(schemas) == 2
    for s in schemas:
        assert s["is_core"] == 1
        assert s["source_type"] == "consolidation"
        members = json.loads(s["evidence"])
        assert len(members) >= 3
        # provenance edges exist for every member
        for mid in members:
            e = conn.execute(
                "SELECT * FROM edges WHERE from_id=? AND to_id=? "
                "AND edge_type='consolidates'", (s["id"], mid),
            ).fetchone()
            assert e is not None
        # schema node has an embedding (medoid's blob)
        emb = conn.execute(
            "SELECT embedding_blob FROM memory_embeddings WHERE memory_id=?",
            (s["id"],),
        ).fetchone()
        assert emb is not None and emb["embedding_blob"]

    # members demoted, not deleted, text untouched
    for mid in ids["a"] + ids["b"]:
        row = conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        assert row is not None
        assert row["is_core"] == 0
        assert row["status"] == "durable"


def test_apply_is_not_repeatable_on_same_members(fresh_db, monkeypatch):
    """Once consolidated, members carry a 'consolidates' edge and drop out
    of eligibility, so a second analyze proposes nothing new."""
    conn, cama_mcp = fresh_db
    monkeypatch.setattr(consol, "DB_PATH", cama_mcp.DB_PATH)
    _seed(conn)

    first = consol.analyze("core", threshold=0.92, min_size=3)
    consol.apply_report(first)
    second = consol.analyze("core", threshold=0.92, min_size=3)

    assert second["clusters_found"] == 0
