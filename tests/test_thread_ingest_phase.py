"""Tests for the incremental thread import and the sleep daemon's ingest phase.

Two promises: a quiet cycle does not re-walk gigabytes of archives, and a
broken importer never takes the sleep cycle down with it.
"""

import json
import os
import sqlite3
import sys

import pytest

from cama.ingest import cama_import_threads as threads


class _StubEncoder:
    def encode(self, text, **kw):
        import numpy as np

        return np.zeros(384, dtype="float32")


def _transcript(home, name, turns):
    """Write a Claude Code transcript under a fake profile."""
    d = home / ".claude" / "projects" / "C--Users-Test"
    d.mkdir(parents=True, exist_ok=True)
    records = []
    for i, (user, reply) in enumerate(turns):
        records.append({"type": "user", "uuid": f"{name}-h{i}", "sessionId": name,
                        "timestamp": f"2026-08-0{i + 1}T12:00:00.000Z",
                        "origin": {"kind": "human"}, "entrypoint": "claude-desktop",
                        "message": {"role": "user", "content": user}})
        records.append({"type": "assistant", "uuid": f"{name}-a{i}", "sessionId": name,
                        "timestamp": f"2026-08-0{i + 1}T12:00:05.000Z",
                        "message": {"role": "assistant",
                                    "content": [{"type": "text", "text": reply}]}})
    p = d / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMA_THREAD_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def db(tmp_path, monkeypatch):
    from schema_builder import init_production_memory_schema

    path = tmp_path / "memory.db"
    monkeypatch.setenv("CAMA_DB_PATH", str(path))
    init_production_memory_schema(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


def _count(conn):
    return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


# ---------------------------------------------------------------- incremental pass

def test_first_pass_imports_and_records_state(home, db, tmp_path):
    _transcript(home, "s1", [("hello", "hi")])
    state = tmp_path / "state.json"
    out = threads.import_new_threads(db, encoder=_StubEncoder(), state_path=str(state))
    assert out["written"] == 1
    assert out["embedded"] == 1
    assert out["files_walked"] == 1
    assert _count(db) == 1
    recorded = threads.load_state(str(state))
    assert len(recorded) == 1
    assert list(recorded)[0].endswith("s1.jsonl")


def test_unchanged_files_are_not_walked_again(home, db, tmp_path):
    _transcript(home, "s1", [("hello", "hi")])
    state = str(tmp_path / "state.json")
    threads.import_new_threads(db, encoder=_StubEncoder(), state_path=state)
    again = threads.import_new_threads(db, encoder=_StubEncoder(), state_path=state)
    assert again["files_walked"] == 0
    assert again["written"] == 0
    assert _count(db) == 1


def test_modified_file_is_rewalked_and_only_new_turns_land(home, db, tmp_path):
    p = _transcript(home, "s1", [("hello", "hi")])
    state = str(tmp_path / "state.json")
    threads.import_new_threads(db, encoder=_StubEncoder(), state_path=state)

    _transcript(home, "s1", [("hello", "hi"), ("second thing", "sure")])
    os.utime(p, (os.path.getmtime(p) + 10, os.path.getmtime(p) + 10))

    out = threads.import_new_threads(db, encoder=_StubEncoder(), state_path=state)
    assert out["files_walked"] == 1
    assert out["written"] == 1, "the turn already stored must not be duplicated"
    assert out["skipped"] == 1
    assert _count(db) == 2


def test_a_new_session_file_is_picked_up(home, db, tmp_path):
    _transcript(home, "s1", [("one", "1")])
    state = str(tmp_path / "state.json")
    threads.import_new_threads(db, encoder=_StubEncoder(), state_path=state)
    _transcript(home, "s2", [("two", "2")])
    out = threads.import_new_threads(db, encoder=_StubEncoder(), state_path=state)
    assert out["files_walked"] == 1
    assert out["written"] == 1
    assert _count(db) == 2


def test_state_file_survives_garbage(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert threads.load_state(str(p)) == {}
    p.write_text(json.dumps({"a": "not-a-number", "b": 12.5}), encoding="utf-8")
    assert threads.load_state(str(p)) == {"b": 12.5}


def test_rows_land_without_an_encoder(home, db, tmp_path):
    _transcript(home, "s1", [("hello", "hi")])
    out = threads.import_new_threads(db, embed=False, state_path=str(tmp_path / "s.json"))
    assert out["written"] == 1
    assert out["embedded"] == 0


# ---------------------------------------------------------------- the daemon phase

def test_ingest_phase_never_raises(db, monkeypatch, tmp_path):
    from cama.sleep import cama_sleep

    monkeypatch.setattr(cama_sleep, "DB_PATH", str(tmp_path / "memory.db"))

    def boom(*a, **k):
        raise RuntimeError("archive unreadable")

    monkeypatch.setattr(threads, "import_new_threads", boom)
    out = cama_sleep.ingest_new_threads(db)
    assert out["written"] == 0
    assert "archive unreadable" in out["error"]


def test_ingest_phase_writes_without_sentence_transformers(home, db, monkeypatch, tmp_path):
    from cama.sleep import cama_sleep

    monkeypatch.setattr(cama_sleep, "DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import fails
    _transcript(home, "s1", [("hello", "hi")])
    out = cama_sleep.ingest_new_threads(db)
    assert out["written"] == 1
    assert out["embedded"] == 0
    assert _count(db) == 1
