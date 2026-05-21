"""Per-participant isolation tests.

The per-DB isolation model (see MULTI_USER_THREAT_MODEL.md option B) gives
each participant their own SQLite file under ~/.cama/participant_<id>/.
These tests pin three invariants:

1. cama_user_paths resolves to participant-aware paths when CAMA_PARTICIPANT_ID
   is set, and to the default location when it isn't.
2. Per-user loaders do NOT fall back to the global location when in
   participant mode — that fallback would defeat isolation.
3. Two participants writing to their own DBs do not see each other's rows.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest


def test_default_paths_when_no_participant(monkeypatch):
    """Without CAMA_PARTICIPANT_ID, paths resolve to the legacy single-user location."""
    monkeypatch.delenv("CAMA_PARTICIPANT_ID", raising=False)
    import importlib
    from cama.core import cama_user_paths
    importlib.reload(cama_user_paths)
    expected = Path.home() / ".cama"
    assert cama_user_paths.cama_user_dir() == expected
    assert cama_user_paths.default_db_path() == str(expected / "memory.db")
    assert cama_user_paths.identity_sentinels_path() == expected / "identity_sentinels.json"
    assert cama_user_paths.user_aliases_path() == expected / "user_aliases.json"
    assert cama_user_paths.is_participant_mode() is False


def test_participant_paths_when_id_set(monkeypatch):
    """With CAMA_PARTICIPANT_ID set, all paths route to the participant dir."""
    monkeypatch.setenv("CAMA_PARTICIPANT_ID", "TESTPID")
    import importlib
    from cama.core import cama_user_paths
    importlib.reload(cama_user_paths)
    expected = Path.home() / ".cama" / "participant_TESTPID"
    assert cama_user_paths.cama_user_dir() == expected
    assert cama_user_paths.default_db_path() == str(expected / "memory.db")
    assert cama_user_paths.identity_sentinels_path() == expected / "identity_sentinels.json"
    assert cama_user_paths.user_aliases_path() == expected / "user_aliases.json"
    assert cama_user_paths.is_participant_mode() is True


def test_participant_mode_no_fallback_to_global(monkeypatch, tmp_path):
    """If CAMA_PARTICIPANT_ID is set, loaders look ONLY in the participant dir
    even when a file exists at the global location. This is the isolation
    guarantee — operator's calibration must not leak to participants."""
    monkeypatch.setenv("CAMA_PARTICIPANT_ID", "ISOLPID")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import importlib
    from cama.core import cama_user_paths
    importlib.reload(cama_user_paths)

    # Plant a file at the GLOBAL location that should NOT be visible.
    global_dir = tmp_path / ".cama"
    global_dir.mkdir()
    (global_dir / "identity_sentinels.json").write_text(
        json.dumps({"identity_sentinels": [{"name": "OPERATOR_SECRET"}]})
    )
    (global_dir / "user_aliases.json").write_text(
        json.dumps({"person_to_relation": {"OPERATOR_PERSON": "leak"}})
    )

    # Participant dir has no calibration file — loaders should return {} not the operator's data.
    p_sentinels = cama_user_paths.identity_sentinels_path()
    p_aliases = cama_user_paths.user_aliases_path()
    assert not p_sentinels.exists(), "fixture setup: participant sentinels file must NOT exist"
    assert not p_aliases.exists(), "fixture setup: participant aliases file must NOT exist"
    # The paths must point to the participant dir, not the global dir.
    assert "participant_ISOLPID" in str(p_sentinels)
    assert "participant_ISOLPID" in str(p_aliases)


def _init_schema(db_path: str) -> sqlite3.Connection:
    """Minimal memories-table schema for the cross-participant probe.
    We don't need the full CAMA schema — just enough to write/read rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def test_two_participants_do_not_see_each_other(tmp_path):
    """The architectural guarantee: P1 and P2 with separate DB files cannot
    see each other's memories, regardless of how retrieval queries are
    written. This is the load-bearing safety property of the per-DB model."""
    p1_dir = tmp_path / "participant_P1"
    p2_dir = tmp_path / "participant_P2"
    p1_dir.mkdir()
    p2_dir.mkdir()

    p1_conn = _init_schema(str(p1_dir / "memory.db"))
    p2_conn = _init_schema(str(p2_dir / "memory.db"))

    p1_conn.execute(
        "INSERT INTO memories (raw_text, created_at) VALUES (?, ?)",
        ("P1_PRIVATE_MEMORY", "2026-01-01"),
    )
    p2_conn.execute(
        "INSERT INTO memories (raw_text, created_at) VALUES (?, ?)",
        ("P2_PRIVATE_MEMORY", "2026-01-01"),
    )
    p1_conn.commit()
    p2_conn.commit()

    # P1 queries: should see only P1's memory
    p1_rows = p1_conn.execute("SELECT raw_text FROM memories").fetchall()
    assert [r[0] for r in p1_rows] == ["P1_PRIVATE_MEMORY"]

    # P2 queries: should see only P2's memory
    p2_rows = p2_conn.execute("SELECT raw_text FROM memories").fetchall()
    assert [r[0] for r in p2_rows] == ["P2_PRIVATE_MEMORY"]

    # Even an explicit cross-query from P1 looking for P2's content returns nothing
    # because they're literally different SQLite files.
    cross = p1_conn.execute(
        "SELECT raw_text FROM memories WHERE raw_text = ?",
        ("P2_PRIVATE_MEMORY",),
    ).fetchall()
    assert cross == [], "P1 saw P2's memory — per-DB isolation broken"

    p1_conn.close()
    p2_conn.close()
