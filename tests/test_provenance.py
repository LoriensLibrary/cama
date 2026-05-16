"""Provenance discipline tests.

The README's central claim:

    "Teachings are authoritative memory. Inferences are hypotheses with a half-life."

This means every row in `memories` must carry honest provenance metadata.
Teachings come from the user and are durable; inferences come from the
assistant and are provisional until the user confirms. These tests pin the
schema defaults and round-trip a few representative writes to verify the
discipline holds at the storage layer.
"""


def _insert(conn, **kwargs):
    """Insert a row into memories, returning the new id.

    Unspecified columns fall back to the schema defaults - which is exactly
    what we want to assert against.
    """
    raw_text = kwargs.pop("raw_text", "test memory")
    created_at = kwargs.pop("created_at", "2026-05-16T00:00:00+00:00")
    cols = ["raw_text", "created_at", "updated_at"] + list(kwargs.keys())
    vals = [raw_text, created_at, created_at] + list(kwargs.values())
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO memories ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    return cur.lastrowid


def _fetch(conn, mid):
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
    return dict(row)


def test_default_insert_is_a_teaching(fresh_db):
    """Bare insert should look like a user teaching: durable + user-authored."""
    conn, _ = fresh_db
    mid = _insert(conn, raw_text="user said this")
    row = _fetch(conn, mid)
    assert row["source_type"] == "teaching"
    assert row["status"] == "durable"
    assert row["proposed_by"] == "user"


def test_inference_stays_provisional(fresh_db):
    """An assistant inference must be storable as provisional/proposed_by=assistant."""
    conn, _ = fresh_db
    mid = _insert(
        conn,
        raw_text="model guessed this",
        source_type="inference",
        status="provisional",
        proposed_by="assistant",
    )
    row = _fetch(conn, mid)
    assert row["source_type"] == "inference"
    assert row["status"] == "provisional"
    assert row["proposed_by"] == "assistant"


def test_status_weight_function_reflects_discipline(fresh_db):
    """_status_weight encodes the write-discipline weighting:
    durable and provisional carry weight; expired/rejected do not."""
    _, cama_mcp = fresh_db
    assert cama_mcp._status_weight("durable") == 1.0
    assert cama_mcp._status_weight("provisional") == 1.0
    assert cama_mcp._status_weight("expired") == 0.0
    assert cama_mcp._status_weight("rejected") == 0.0


def test_counterweight_field_round_trips(fresh_db):
    """Anti-spiral counterweight memories carry a counterweight_type tag."""
    conn, _ = fresh_db
    mid = _insert(
        conn,
        raw_text="reminder that things got better last winter",
        counterweight_type="resilience",
    )
    row = _fetch(conn, mid)
    assert row["counterweight_type"] == "resilience"


def test_provenance_columns_are_not_nullable(fresh_db):
    """source_type, status, and proposed_by have NOT NULL constraints."""
    conn, _ = fresh_db
    info = conn.execute("PRAGMA table_info(memories)").fetchall()
    notnull = {row[1]: row[3] for row in info}  # name -> notnull flag
    assert notnull["raw_text"] == 1
    assert notnull["source_type"] == 1
    assert notnull["status"] == 1
    assert notnull["proposed_by"] == 1
