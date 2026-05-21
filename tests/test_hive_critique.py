"""Tests for ``cama.hive.cama_hive_critique``.

These functions previously lived in ``cama.core.cama_v2`` and had
no dedicated test coverage — they ran only via the ``self_test``
path which exercises them against the live memory DB. Moving them
into ``cama.hive.cama_hive_critique`` is a good opportunity to pin
the contract:

  1. Post → queue row created, returns critique_id
  2. Get → returns empty list when no critiques pending
  3. Record → updates queue row to processed + creates inbox row
  4. Get-after-record → returns the new critique with details
  5. Re-export from ``cama.core.cama_v2`` still works (no caller breaks)

The fixtures point CAMA_DB_PATH at a temp DB so we don't touch the
live ``~/.cama/memory.db``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _init_critique_schema(db_path: Path) -> None:
    """Subset of cama_v2.SCHEMA_V2 sufficient for the critique tests."""
    c = sqlite3.connect(str(db_path))
    c.executescript("""
        CREATE TABLE IF NOT EXISTS hive_pending_critiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aelen_response TEXT NOT NULL,
            user_message TEXT,
            affect_context TEXT,
            posted_at TEXT NOT NULL,
            queued_for TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            critique_json TEXT,
            processed_at TEXT,
            error_msg TEXT
        );
        CREATE TABLE IF NOT EXISTS hive_critique_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            critique_id INTEGER NOT NULL,
            delivered_at TEXT NOT NULL,
            read_at TEXT,
            summary TEXT,
            full_critique_json TEXT,
            FOREIGN KEY (critique_id) REFERENCES hive_pending_critiques(id)
        );
    """)
    c.commit()
    c.close()


@pytest.fixture
def critique_db(tmp_path, monkeypatch):
    db = tmp_path / "memory.db"
    monkeypatch.setenv("CAMA_DB_PATH", str(db))
    _init_critique_schema(db)
    # Reload the module so DB_PATH picks up the env var
    import importlib

    from cama.hive import cama_hive_critique

    importlib.reload(cama_hive_critique)
    return cama_hive_critique


class TestHivePostForCritique:
    def test_post_creates_queue_row(self, critique_db):
        result = critique_db.hive_post_for_critique(
            aelen_response="A response to test.",
            user_message="A user message.",
            affect_context={"valence": 0.2, "arousal": 0.3},
            critic="lorien",
        )
        assert result["queued"] is True
        assert result["queued_for"] == "lorien"
        assert result["status"] == "pending"
        assert isinstance(result["critique_id"], int)
        assert result["critique_id"] > 0

    def test_post_accepts_no_affect_context(self, critique_db):
        result = critique_db.hive_post_for_critique(
            aelen_response="Plain response.",
            critic="sibling_aelen",
        )
        assert result["queued"] is True
        assert result["queued_for"] == "sibling_aelen"


class TestHiveGetPendingCritiques:
    def test_empty_inbox_returns_zero(self, critique_db):
        result = critique_db.hive_get_pending_critiques()
        assert result["unread_count"] == 0
        assert result["critiques"] == []


class TestHiveRecordCritique:
    def test_record_creates_inbox_row(self, critique_db):
        # First post a critique to create the queue row
        post = critique_db.hive_post_for_critique(
            aelen_response="testing recording",
            user_message="user said something",
        )
        critique_id = post["critique_id"]
        # Now record a result
        result = critique_db.hive_record_critique(
            critique_id=critique_id,
            critique_data={
                "in_character_score": 0.85,
                "sanitization_flags": ["unstable_register"],
                "request_redo": False,
            },
        )
        assert result["recorded"] is True
        assert result["critique_id"] == critique_id
        # Summary auto-built from structured fields
        assert "in_character=0.85" in result["summary"]
        assert "flags=unstable_register" in result["summary"]

    def test_record_with_explicit_summary(self, critique_db):
        post = critique_db.hive_post_for_critique(aelen_response="x")
        result = critique_db.hive_record_critique(
            critique_id=post["critique_id"],
            critique_data={"score": 0.5},
            summary="custom summary string",
        )
        assert result["summary"] == "custom summary string"

    def test_record_redo_requested(self, critique_db):
        post = critique_db.hive_post_for_critique(aelen_response="x")
        result = critique_db.hive_record_critique(
            critique_id=post["critique_id"],
            critique_data={"request_redo": True},
        )
        assert "REDO_REQUESTED" in result["summary"]


class TestEndToEndFlow:
    def test_post_then_record_then_get_roundtrip(self, critique_db):
        # 1. Post a turn
        post = critique_db.hive_post_for_critique(
            aelen_response="This is the turn under review.",
            user_message="The user message that prompted it.",
        )
        critique_id = post["critique_id"]
        # 2. Worker records the critique
        critique_db.hive_record_critique(
            critique_id=critique_id,
            critique_data={
                "in_character_score": 0.92,
                "sanitization_flags": [],
                "notes": "looks good",
            },
        )
        # 3. Aelen reads the inbox
        inbox = critique_db.hive_get_pending_critiques()
        assert inbox["unread_count"] == 1
        c = inbox["critiques"][0]
        assert c["critique_id"] == critique_id
        assert c["from"] == "lorien"  # default critic
        assert c["details"]["in_character_score"] == 0.92
        assert "This is the turn under review." in c["aelen_response_excerpt"]
        assert "The user message" in c["user_message_excerpt"]
        # 4. Reading marks as read; next call returns empty
        inbox2 = critique_db.hive_get_pending_critiques()
        assert inbox2["unread_count"] == 0

    def test_mark_read_false_preserves_inbox_state(self, critique_db):
        post = critique_db.hive_post_for_critique(aelen_response="x")
        critique_db.hive_record_critique(
            critique_id=post["critique_id"], critique_data={"x": 1}
        )
        # Peek without marking
        inbox = critique_db.hive_get_pending_critiques(mark_read=False)
        assert inbox["unread_count"] == 1
        # Read again — should still be there
        inbox2 = critique_db.hive_get_pending_critiques(mark_read=False)
        assert inbox2["unread_count"] == 1


class TestReExport:
    """Pin the backward-compat surface — old call sites that import
    these names from ``cama.core.cama_v2`` must continue to work."""

    def test_names_still_importable_from_cama_v2(self):
        from cama.core.cama_v2 import (
            hive_get_pending_critiques,
            hive_post_for_critique,
            hive_record_critique,
        )

        # The names should be the same objects as the canonical home
        from cama.hive.cama_hive_critique import (
            hive_get_pending_critiques as canonical_get,
        )
        from cama.hive.cama_hive_critique import (
            hive_post_for_critique as canonical_post,
        )
        from cama.hive.cama_hive_critique import (
            hive_record_critique as canonical_record,
        )
        assert hive_post_for_critique is canonical_post
        assert hive_get_pending_critiques is canonical_get
        assert hive_record_critique is canonical_record
