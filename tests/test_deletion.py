"""Deletion-completeness tests.

CAMA's data-handling claims (see DATA_HANDLING.md) depend on memory deletion
actually removing the memory's embedding row, not just the row in `memories`.
The schema declares ON DELETE CASCADE on memory_embeddings.memory_id, but
SQLite enforces foreign keys only when PRAGMA foreign_keys = ON is set on
the connection. These tests pin that contract so the deletion guarantee
cannot silently regress.
"""

import json


def _insert_memory_with_embedding(conn, raw_text: str = "test memory"):
    """Insert a memory and a matching embedding row. Returns memory_id."""
    cur = conn.execute(
        "INSERT INTO memories (raw_text, memory_type, source_type, status, "
        "proposed_by, evidence, confidence, review_after, needs_user_confirmation, "
        "is_core, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (raw_text, "experience", "teaching", "durable", "user",
         "[]", 1.0, None, 0, 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    )
    mid = cur.lastrowid
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_json, model, computed_at) "
        "VALUES (?,?,?,?)",
        (mid, json.dumps([0.0] * 8), "test-model", "2026-01-01T00:00:00Z")
    )
    conn.commit()
    return mid


def test_foreign_keys_enabled(fresh_db):
    """SQLite enforces FK cascades only with PRAGMA foreign_keys = ON.
    Without this pragma, every cascade declared in the schema silently no-ops."""
    conn, _ = fresh_db
    enforced = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert enforced == 1, "PRAGMA foreign_keys must be ON for cascade deletes to work"


def test_memory_delete_cascades_to_embedding(fresh_db):
    """Deleting a memory must also remove its embedding row.
    Without this, deleted memories leave their semantic fingerprint behind."""
    conn, _ = fresh_db
    mid = _insert_memory_with_embedding(conn, "delete-cascade probe")

    # Pre-delete: embedding row exists
    pre = conn.execute(
        "SELECT memory_id FROM memory_embeddings WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert pre is not None, "fixture setup failed: embedding row not inserted"

    # Delete the memory
    conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    conn.commit()

    # Post-delete: embedding row gone
    post = conn.execute(
        "SELECT memory_id FROM memory_embeddings WHERE memory_id = ?", (mid,)
    ).fetchone()
    assert post is None, (
        f"embedding row for memory_id={mid} survived memory deletion, "
        "cascade is broken (check PRAGMA foreign_keys and FK declaration on memory_embeddings)"
    )


def test_memory_delete_cascades_to_edges(fresh_db):
    """Deleting a memory must also remove edges referencing it from either side.
    Pinned because edges declare ON DELETE CASCADE on both from_id and to_id."""
    conn, _ = fresh_db
    a = _insert_memory_with_embedding(conn, "edge-cascade probe A")
    b = _insert_memory_with_embedding(conn, "edge-cascade probe B")
    conn.execute(
        "INSERT INTO edges (from_id, to_id, edge_type, weight, rationale, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (a, b, "resonance", 0.5, "test", "2026-01-01T00:00:00Z")
    )
    conn.commit()

    conn.execute("DELETE FROM memories WHERE id = ?", (a,))
    conn.commit()

    survivors = conn.execute(
        "SELECT id FROM edges WHERE from_id = ? OR to_id = ?", (a, a)
    ).fetchall()
    assert len(survivors) == 0, "edges referencing deleted memory survived, cascade broken"
