"""Shared pytest fixtures and import-time setup for CAMA tests.

We override CAMA_DB_PATH and CAMA_RING_SIZE before cama_mcp is imported so the
module never touches the real ~/.cama/memory.db. Each test then gets its own
fresh SQLite file via the `fresh_db` fixture.

For multi-tenant dyad tests we also build a schema-only template DB once per
session and set CAMA_TEMPLATE_DB to point at it. In production cama_dyad
uses the user's existing CAMA as the schema template; in CI no such file
exists, so we construct one from cama_mcp's standard schema initialization.
"""

import os
import sys
import tempfile
from pathlib import Path

# Set test DB path BEFORE any test imports cama_mcp. Module-level
# DB_PATH = os.environ.get("CAMA_DB_PATH", ...) reads this once on import.
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="cama_test_"))
os.environ.setdefault("CAMA_DB_PATH", str(_TEST_DB_DIR / "import_time.db"))
os.environ.setdefault("CAMA_RING_SIZE", "5")

# Make the repo root importable so `import cama_mcp` works under pytest.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Build a schema-only template DB for the multi-tenant dyad tests.
# Always use the test-built template (don't honor a pre-set
# CAMA_TEMPLATE_DB) so tests are deterministic across developer
# machines and CI.
_TEMPLATE_DB = _TEST_DB_DIR / "schema_template.db"
import cama_mcp  # noqa: E402

_saved_db_path = cama_mcp.DB_PATH
cama_mcp.DB_PATH = str(_TEMPLATE_DB)
try:
    _conn = cama_mcp.get_db()
    _conn.close()
finally:
    cama_mcp.DB_PATH = _saved_db_path
os.environ["CAMA_TEMPLATE_DB"] = str(_TEMPLATE_DB)

import pytest  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Yield (sqlite3.Connection, cama_mcp module) backed by a fresh temp DB.

    The DB file lives in pytest's tmp_path so it's auto-cleaned between tests.
    cama_mcp's module-level DB_PATH is monkeypatched for the duration of the
    test so get_db() / _init() use this file.
    """
    db_file = tmp_path / "cama.db"
    import cama_mcp

    monkeypatch.setattr(cama_mcp, "DB_PATH", str(db_file))
    conn = cama_mcp.get_db()
    try:
        yield conn, cama_mcp
    finally:
        conn.close()
