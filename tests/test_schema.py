"""Schema integrity tests.

The README documents three memory layers (Shelves / Racks / Console) backed
by a specific set of SQLite tables. These tests pin that contract so any
schema change that would break the published architecture trips a test.
"""


EXPECTED_TABLES = {
    "memories",
    "memory_affect",
    "memory_embeddings",
    "edges",
    "ring",
    "islands",
    "island_members",
    "people",
    "songs",
    "aelen_state",
    "daily_context",
    "session_compliance",
}

REQUIRED_MEMORY_COLUMNS = {
    "id",
    "raw_text",
    "memory_type",
    "source_type",
    "status",
    "proposed_by",
    "counterweight_type",
    "consent_level",
    "created_at",
    "updated_at",
}


def _tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_init_creates_all_documented_tables(fresh_db):
    conn, _ = fresh_db
    tables = _tables(conn)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


def test_memories_table_has_provenance_columns(fresh_db):
    """README claims provenance-aware writes: source_type, status, proposed_by."""
    conn, _ = fresh_db
    cols = _columns(conn, "memories")
    missing = REQUIRED_MEMORY_COLUMNS - cols
    assert not missing, f"memories table missing required columns: {missing}"


def test_ring_buffer_has_slot_primary_key(fresh_db):
    """Console layer is a circular buffer keyed by slot."""
    conn, _ = fresh_db
    info = conn.execute("PRAGMA table_info(ring)").fetchall()
    pk_cols = [row[1] for row in info if row[5]]  # row[5] is the 'pk' column
    assert pk_cols == ["slot"], f"ring primary key should be (slot,), got {pk_cols}"


def test_edges_table_supports_typed_relations(fresh_db):
    """Racks layer: typed edges between memories with weight and rationale."""
    conn, _ = fresh_db
    cols = _columns(conn, "edges")
    assert {"from_id", "to_id", "edge_type", "weight"}.issubset(cols)


def test_init_is_idempotent(fresh_db):
    """Calling _init twice on the same connection should not raise."""
    conn, cama_mcp = fresh_db
    cama_mcp._init(conn)
    cama_mcp._init(conn)
    # And the table set should be unchanged
    assert EXPECTED_TABLES.issubset(_tables(conn))
