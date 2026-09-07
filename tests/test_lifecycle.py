"""Lifecycle contract tests, exercised through the public MCP tool functions.

These pin the guarantees the write discipline advertises but never had tests
for: an inference is stored as a provisional inference (not a durable
journal), ring pushes survive the connection closing, rejected and expired
memories leave working memory, and counterweights honor the same sensitivity
gate as ordinary retrieval. Each assertion re-opens the database after the
tool has closed its own connection, so a write that was only ever visible
inside an uncommitted transaction fails here.
"""

import asyncio
import json

import pytest

from mcp_sections import continuity, memory_lifecycle, retrieval

NEG = {"valence": -0.9, "arousal": 0.6, "emotions": {"grief": 1.0, "sadness": 1.0, "fear": 1.0}}


@pytest.fixture
def db(fresh_db, monkeypatch):
    """fresh_db plus no embedding provider, so tests never load a model."""
    conn, cama_mcp = fresh_db
    monkeypatch.setattr(cama_mcp, "EMBEDDING_PROVIDER", "none")
    return conn, cama_mcp


def _run(coro):
    return json.loads(asyncio.run(coro))


def _row(cama_mcp, mid):
    c = cama_mcp.get_db()
    try:
        return c.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    finally:
        c.close()


def _ring_ids(cama_mcp):
    c = cama_mcp.get_db()
    try:
        return {r["memory_id"] for r in c.execute("SELECT memory_id FROM ring").fetchall()}
    finally:
        c.close()


def _store_inference(cama_mcp, text="she prefers plain prose", conf=0.6):
    return _run(memory_lifecycle.cama_store_inference(
        cama_mcp.StoreInferenceInput(raw_text=text, confidence=conf, evidence_quotes=["no bold, no lists"])))


def _store_teaching(cama_mcp, text="teaching text", **kw):
    return _run(memory_lifecycle.cama_store_teaching(cama_mcp.StoreTeachingInput(raw_text=text, **kw)))


def _seed(cama_mcp, text, consent="low", cw=None, core=0, mtype="experience", status="durable"):
    """Direct insert for retrieval fixtures. Returns memory id."""
    c = cama_mcp.get_db()
    try:
        now = cama_mcp._now()
        cur = c.execute(
            "INSERT INTO memories (raw_text, memory_type, source_type, status, proposed_by, consent_level, "
            "counterweight_type, is_core, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (text, mtype, "teaching", status, "user", consent, cw, core, now, now))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


# ---------------------------------------------------------------- store_inference

def test_store_inference_persists_as_provisional_inference(db):
    _, cama_mcp = db
    out = _store_inference(cama_mcp)
    assert out["stored"] is True
    assert out["source_type"] == "inference"
    assert out["status"] == "provisional"
    assert out["needs_user_confirmation"] is True
    row = _row(cama_mcp, out["memory_id"])
    assert row["source_type"] == "inference"
    assert row["status"] == "provisional"
    assert row["proposed_by"] == "assistant"
    assert row["needs_user_confirmation"] == 1
    assert row["review_after"] is None, "inferences carry no expiry by current design"


def test_store_inference_response_reflects_stored_row(db):
    """The response must come from the row, not from a literal that can drift."""
    _, cama_mcp = db
    out = _store_inference(cama_mcp)
    row = _row(cama_mcp, out["memory_id"])
    assert (out["source_type"], out["status"]) == (row["source_type"], row["status"])


def test_store_inference_ring_push_survives_connection_close(db):
    _, cama_mcp = db
    out = _store_inference(cama_mcp)
    assert out["ring_ok"] is True
    assert out["memory_id"] in _ring_ids(cama_mcp)


# ---------------------------------------------------------------- confirm / reject / expire

def test_confirm_promotes_provisional_inference(db):
    _, cama_mcp = db
    mid = _store_inference(cama_mcp)["memory_id"]
    out = _run(memory_lifecycle.cama_confirm_memory(mid))
    # main's confirm also reports quarantine-release fields; pin only ours.
    assert out["promoted"] is True
    assert out["memory_id"] == mid
    assert out["new_status"] == "durable"
    row = _row(cama_mcp, mid)
    assert row["status"] == "durable"
    assert row["needs_user_confirmation"] == 0
    assert row["confidence"] == 1.0


def test_confirm_refuses_non_provisional(db):
    _, cama_mcp = db
    mid = _store_teaching(cama_mcp)["memory_id"]
    out = _run(memory_lifecycle.cama_confirm_memory(mid))
    assert out == {"error": "Already durable"}


def test_reject_marks_rejected_and_evicts_ring(db):
    _, cama_mcp = db
    mid = _store_inference(cama_mcp)["memory_id"]
    assert mid in _ring_ids(cama_mcp)
    out = _run(memory_lifecycle.cama_reject_memory(mid, "she said otherwise"))
    assert out["rejected"] is True
    assert out["ring_evicted"] is True
    assert out["previous_status"] == "provisional"
    row = _row(cama_mcp, mid)
    assert row["status"] == "rejected"
    assert "[REJECTED: she said otherwise]" in row["context"]
    assert mid not in _ring_ids(cama_mcp)


def test_reject_unknown_id_is_an_error(db):
    _, cama_mcp = db
    assert _run(memory_lifecycle.cama_reject_memory(999999)) == {"error": "Not found"}


def test_expire_stale_touches_only_overdue_provisionals_and_evicts_ring(db):
    conn, cama_mcp = db
    fresh = _store_inference(cama_mcp, "no review date")["memory_id"]
    overdue = _store_inference(cama_mcp, "review date in the past")["memory_id"]
    conn.execute("UPDATE memories SET review_after='2000-01-01T00:00:00+00:00' WHERE id=?", (overdue,))
    conn.commit()
    out = _run(memory_lifecycle.cama_expire_stale())
    assert out["expired"] == 1 and out["ids"] == [overdue]
    assert _row(cama_mcp, fresh)["status"] == "provisional"
    assert _row(cama_mcp, overdue)["status"] == "expired"
    ring = _ring_ids(cama_mcp)
    assert fresh in ring and overdue not in ring


# ---------------------------------------------------------------- ring visibility

def test_get_ring_hides_rejected_and_expired(db):
    conn, cama_mcp = db
    keep = _store_inference(cama_mcp, "keep")["memory_id"]
    gone = _store_inference(cama_mcp, "gone")["memory_id"]
    # Simulate a status change that bypassed eviction (older code paths, manual SQL).
    conn.execute("UPDATE memories SET status='expired' WHERE id=?", (gone,))
    conn.commit()
    assert gone in _ring_ids(cama_mcp), "fixture: entry must still physically sit in the ring"
    ids = {m["id"] for m in _run(retrieval.cama_get_ring())["ring"]}
    assert keep in ids and gone not in ids


# ---------------------------------------------------------------- counterweight sensitivity gate

def test_query_counterweights_respect_sensitivity_gate(db):
    _, cama_mcp = db
    secret_cw = _seed(cama_mcp, "high-consent grounding", consent="high", cw="grounding")
    secret_core = _seed(cama_mcp, "high-consent core identity", consent="high", core=1, mtype="identity")
    _seed(cama_mcp, "an ordinary low-consent memory")

    out = _run(retrieval.cama_query_memories(cama_mcp.QueryInput(current_affect=NEG, top_k=1)))
    ids = {m["id"] for m in out["results"]} | {m["id"] for m in out["counterweights"]}
    assert secret_cw not in ids and secret_core not in ids
    assert out["counterweights"] == [], "nothing is eligible once the gate applies"

    out = _run(retrieval.cama_query_memories(cama_mcp.QueryInput(
        current_affect=NEG, top_k=1, filters={"include_sensitive": "true"})))
    assert secret_cw in {m["id"] for m in out["counterweights"]}, "explicit include_sensitive must still surface it"


def test_read_room_never_surfaces_high_sensitivity_and_ring_persists(db):
    _, cama_mcp = db
    secret_cw = _seed(cama_mcp, "high-consent grounding", consent="high", cw="grounding")
    secret_core = _seed(cama_mcp, "high-consent core", consent="high", core=1, mtype="breakthrough")
    _seed(cama_mcp, "an ordinary durable memory")
    _seed(cama_mcp, "another ordinary durable memory")

    out = _run(retrieval.cama_read_room(cama_mcp.ReadRoomInput(current_affect=NEG)))
    assert out["negative"] is True
    ids = {m["id"] for m in out["memories"]} | {m["id"] for m in out["counterweights"]}
    assert secret_cw not in ids and secret_core not in ids
    assert _ring_ids(cama_mcp), "read_room ring pushes must be committed"


def test_thread_start_ring_pushes_persist_and_counterweights_stay_gated(db):
    _, cama_mcp = db
    secret_cw = _seed(cama_mcp, "high-consent grounding", consent="high", cw="grounding")
    for i in range(3):
        _seed(cama_mcp, f"ordinary durable memory {i}")

    out = _run(continuity.cama_thread_start(user_message="rough morning", user_affect=NEG))
    assert out.get("boot_source") == "warm_boot_v2"
    assert secret_cw not in {m["id"] for m in out.get("counterweights", [])}
    assert _ring_ids(cama_mcp), "thread_start ring pushes must be committed"


# ---------------------------------------------------------------- delete cascade through the tool

def test_delete_memory_cascades_through_public_tool(db):
    conn, cama_mcp = db
    a = _store_teaching(cama_mcp, "A", emotions={"warmth": 0.5})["memory_id"]
    b = _store_teaching(cama_mcp, "B")["memory_id"]
    conn.execute("INSERT INTO edges (from_id,to_id,edge_type,weight,rationale,created_at) VALUES (?,?,?,?,?,?)",
                 (a, b, "resonance", 0.5, "test", cama_mcp._now()))
    conn.commit()
    assert _run(memory_lifecycle.cama_delete_memory(a)) == {"deleted": True, "memory_id": a}
    assert _row(cama_mcp, a) is None
    c = cama_mcp.get_db()
    try:
        assert c.execute("SELECT COUNT(*) FROM memory_affect WHERE memory_id=?", (a,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM edges WHERE from_id=? OR to_id=?", (a, a)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM ring WHERE memory_id=?", (a,)).fetchone()[0] == 0
    finally:
        c.close()
