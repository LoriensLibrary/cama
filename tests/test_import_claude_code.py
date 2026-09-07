"""Tests for the Claude Code transcript importer.

The risks worth pinning are all about what counts as a turn. The
``type: "user"`` envelope carries both real human messages and tool
results; subagent traffic looks like a conversation but is one assistant
talking to another; and re-running the import must not duplicate
anything, because the target is a real memory store.
"""

import json
import sqlite3

import pytest

from cama.ingest import cama_import_claude_code as imp


def _rec(**kw):
    base = {"sessionId": "sess-1", "uuid": f"u{len(kw)}", "timestamp": "2026-08-01T12:00:00.000Z",
            "entrypoint": "claude-desktop", "cwd": "C:\\Users\\Angela", "isSidechain": False}
    base.update(kw)
    return base


def human(text, uuid="h1", ts="2026-08-01T12:00:00.000Z", **kw):
    return _rec(type="user", uuid=uuid, timestamp=ts, origin={"kind": "human"},
                message={"role": "user", "content": text}, **kw)


def assistant(text, uuid="a1", **kw):
    return _rec(type="assistant", uuid=uuid,
                message={"role": "assistant", "content": [{"type": "text", "text": text}]}, **kw)


def tool_result(uuid="t1"):
    return _rec(type="user", uuid=uuid, toolUseResult={"stdout": "secret output"},
                message={"role": "user",
                         "content": [{"type": "tool_result", "content": "secret output"}]})


def write_transcript(tmp_path, records, name="sess-1.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- turn detection

def test_pairs_a_human_turn_with_the_reply(tmp_path):
    path = write_transcript(tmp_path, [
        human("build the thing", uuid="h1"),
        assistant("Building it now."),
    ])
    turns = imp.extract_turns(path)
    assert len(turns) == 1
    assert turns[0]["user_text"] == "build the thing"
    assert turns[0]["assistant_text"] == "Building it now."
    assert turns[0]["source_msg_id"] == "cc:sess-1:h1"


def test_tool_results_are_not_turns(tmp_path):
    """A tool result wears the user envelope. Importing one would put
    command output and file contents into the memory store."""
    path = write_transcript(tmp_path, [
        human("run the tests", uuid="h1"),
        assistant("Running them."),
        tool_result(),
        assistant("They pass."),
    ])
    turns = imp.extract_turns(path)
    assert len(turns) == 1
    assert "secret output" not in turns[0]["assistant_text"]
    assert turns[0]["assistant_text"] == "Running them.\n\nThey pass."


def test_subagent_traffic_is_excluded(tmp_path):
    path = write_transcript(tmp_path, [
        human("go", uuid="h1"),
        assistant("On it."),
        human("subagent prompt", uuid="h2", isSidechain=True),
        assistant("subagent reply", uuid="a2", isSidechain=True),
    ])
    turns = imp.extract_turns(path)
    assert len(turns) == 1
    assert "subagent" not in turns[0]["assistant_text"]


def test_harness_injected_records_are_excluded(tmp_path):
    path = write_transcript(tmp_path, [
        _rec(type="user", uuid="m1", isMeta=True, origin={"kind": "human"},
             message={"role": "user", "content": "<system-reminder>injected</system-reminder>"}),
        human("a real question", uuid="h1"),
        assistant("a real answer"),
    ])
    turns = imp.extract_turns(path)
    assert len(turns) == 1
    assert turns[0]["user_text"] == "a real question"


def test_tool_calls_are_never_imported(tmp_path):
    path = write_transcript(tmp_path, [
        human("read the file", uuid="h1"),
        _rec(type="assistant", uuid="a1", message={"role": "assistant", "content": [
            {"type": "text", "text": "Reading it."},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "C:\\secrets.txt"}},
        ]}),
    ])
    turns = imp.extract_turns(path)
    assert turns[0]["assistant_text"] == "Reading it."
    assert "secrets.txt" not in turns[0]["assistant_text"]


def test_thinking_excluded_by_default_and_included_on_request(tmp_path):
    records = [
        human("why", uuid="h1"),
        _rec(type="assistant", uuid="a1", message={"role": "assistant", "content": [
            {"type": "thinking", "thinking": "internal reasoning"},
            {"type": "text", "text": "Because of X."},
        ]}),
    ]
    path = write_transcript(tmp_path, records)
    assert "internal reasoning" not in imp.extract_turns(path)[0]["assistant_text"]
    with_thinking = imp.extract_turns(path, include_thinking=True)[0]["assistant_text"]
    assert "internal reasoning" in with_thinking
    assert "Because of X." in with_thinking


def test_a_turn_with_no_reply_is_still_kept(tmp_path):
    """The last thing she said before a session ended is still something
    she said."""
    path = write_transcript(tmp_path, [human("one last thing", uuid="h1")])
    turns = imp.extract_turns(path)
    assert len(turns) == 1
    assert turns[0]["assistant_text"] == ""


def test_truncated_final_line_does_not_crash(tmp_path):
    p = tmp_path / "sess-1.jsonl"
    p.write_text(json.dumps(human("complete", uuid="h1")) + "\n" + '{"type": "assis',
                 encoding="utf-8")
    turns = imp.extract_turns(str(p))
    assert len(turns) == 1


# ---------------------------------------------------------------- memory shape

def test_memory_is_an_exchange_with_the_real_timestamp(tmp_path):
    path = write_transcript(tmp_path, [
        human("hello", uuid="h1", ts="2026-07-04T09:30:00.000Z"),
        assistant("hi"),
    ])
    m = imp.build_memory(imp.extract_turns(path)[0])
    assert m["source_type"] == "exchange"
    assert m["status"] == "durable"
    assert m["proposed_by"] == "system"
    assert m["raw_text"] == "[USER] hello\n[ASSISTANT] hi"
    assert m["created_at"].startswith("2026-07-04T09:30:00")
    assert m["created_at"] == m["updated_at"]
    assert "[thread:sess-1]" in m["context"]


def test_import_is_not_labeled_as_an_inference(tmp_path):
    """The platform importer labels every assistant message
    source_type='inference', which is why 23,868 rows in the live store
    read as hypotheses Aelen formed. A transcript of what was said is an
    exchange."""
    path = write_transcript(tmp_path, [human("x", uuid="h1"), assistant("y")])
    assert imp.build_memory(imp.extract_turns(path)[0])["source_type"] != "inference"


# ---------------------------------------------------------------- writing

@pytest.fixture
def db(tmp_path, monkeypatch):
    from schema_builder import init_production_memory_schema

    path = tmp_path / "memory.db"
    monkeypatch.setenv("CAMA_DB_PATH", str(path))
    init_production_memory_schema(path)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


def _two_memories(tmp_path):
    path = write_transcript(tmp_path, [
        human("first", uuid="h1"), assistant("1"),
        human("second", uuid="h2"), assistant("2"),
    ])
    return [imp.build_memory(t) for t in imp.extract_turns(path)]


def test_dry_run_writes_nothing(db, tmp_path):
    result = imp.write_memories(db, _two_memories(tmp_path), apply=False)
    assert result["would_write"] == 2
    assert result["written"] == 0
    assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_apply_writes_rows_and_affect(db, tmp_path):
    result = imp.write_memories(db, _two_memories(tmp_path), apply=True)
    assert result["written"] == 2
    rows = db.execute(
        "SELECT source_type, status, source_msg_id FROM memories ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["exchange", "exchange"]
    assert [r[2] for r in rows] == ["cc:sess-1:h1", "cc:sess-1:h2"]
    assert db.execute("SELECT COUNT(*) FROM memory_affect").fetchone()[0] == 2


def test_reimport_is_idempotent(db, tmp_path):
    mems = _two_memories(tmp_path)
    imp.write_memories(db, mems, apply=True)
    again = imp.write_memories(db, mems, apply=True)
    assert again["written"] == 0
    assert again["skipped"] == 2
    assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2


def test_new_sessions_import_alongside_existing_ones(db, tmp_path):
    imp.write_memories(db, _two_memories(tmp_path), apply=True)
    later = write_transcript(tmp_path, [
        human("third", uuid="h3", ts="2026-09-01T10:00:00.000Z"), assistant("3"),
    ], name="sess-2.jsonl")
    new = [imp.build_memory(t) for t in imp.extract_turns(later)]
    result = imp.write_memories(db, new, apply=True)
    assert result["written"] == 1
    assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3


def test_collect_walks_a_directory_and_honors_since(tmp_path):
    write_transcript(tmp_path, [
        human("old", uuid="h1", ts="2026-01-01T00:00:00.000Z"), assistant("a"),
    ], name="old.jsonl")
    write_transcript(tmp_path, [
        human("new", uuid="h2", ts="2026-09-01T00:00:00.000Z"), assistant("b"),
    ], name="new.jsonl")
    assert len(imp.collect(str(tmp_path))) == 2
    recent = imp.collect(str(tmp_path), since="2026-06-01")
    assert len(recent) == 1
    assert "new" in recent[0]["raw_text"]


def test_embed_is_off_by_default_and_rows_still_land(db, tmp_path):
    result = imp.write_memories(db, _two_memories(tmp_path), apply=True)
    assert result["written"] == 2
    assert result["embedded"] == 0
    assert db.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0


def test_embed_writes_vectors(db, tmp_path, monkeypatch):
    """Uses a stub encoder so the test does not load a real model."""
    class _Stub:
        def encode(self, text, **kw):
            import numpy as np
            return np.zeros(384, dtype="float32")

    monkeypatch.setattr(imp, "_encoder", lambda: _Stub())
    result = imp.write_memories(db, _two_memories(tmp_path), apply=True, embed=True)
    assert result["written"] == 2
    assert result["embedded"] == 2
    assert db.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 2


def test_a_failing_encoder_does_not_lose_the_row(db, tmp_path, monkeypatch):
    class _Broken:
        def encode(self, text, **kw):
            raise RuntimeError("no model")

    monkeypatch.setattr(imp, "_encoder", lambda: _Broken())
    result = imp.write_memories(db, _two_memories(tmp_path), apply=True, embed=True)
    assert result["written"] == 2
    assert result["embedded"] == 0
