"""Regression cases from the September 2026 live pipeline audit."""
import asyncio
import json
import sqlite3

import pytest

from cama.core import embedding_store as es
from cama.ingest import cama_import_threads as ingest
from cama.ingest.cama_import_claude_code import build_memory, write_memories
from cama.ingest.thread_sources import codex_turns


def event(kind, text, item_id):
    return {"type": "event_msg", "timestamp": "2026-09-25T12:00:00Z", "payload": {
        "type": "item_completed", "item": {"type": kind, "id": item_id,
        "content": [{"type": "text" if kind == "UserMessage" else "Text", "text": text}]}}}


def test_modern_codex_events_exclude_machinery_and_replays(tmp_path):
    path = tmp_path / "rollout-test.jsonl"
    records = [event("UserMessage", "remember the plan", "u1"),
               {"type": "response_item", "payload": {"role": "user", "content": "injected"}},
               event("Reasoning", "private reasoning", "r1"),
               event("McpToolCall", "tool output", "t1"),
               event("AgentMessage", "The plan is saved.", "a1"),
               event("AgentMessage", "The plan is saved.", "a1")]
    path.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    turns = list(codex_turns(str(path)))
    assert len(turns) == 1
    assert turns[0]["user_text"] == "remember the plan"
    assert turns[0]["assistant_text"] == "The plan is saved."


def test_export_uses_selected_branch_and_excludes_private_channels(tmp_path):
    from cama.ingest.thread_sources import openai_export_turns
    def node(parent, role, text, channel=None):
        return {"parent": parent, "message": {"author": {"role": role}, "channel": channel,
                "content": {"parts": [text]}, "create_time": 1 if role == "user" else 2}}
    data = [{"id": "chat", "current_node": "answer", "mapping": {
        "question": node(None, "user", "question"),
        "discarded": node("question", "assistant", "discarded alternative"),
        "reason": node("question", "assistant", "internal", "analysis"),
        "answer": node("reason", "assistant", "selected answer", "final"),
    }}]
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    turns = list(openai_export_turns(str(path)))
    assert len(turns) == 1
    assert turns[0]["assistant_text"] == "selected answer"


def test_malformed_export_is_a_retryable_failure(db, tmp_path, monkeypatch):
    path = tmp_path / "conversations.json"
    path.write_text('{broken')
    monkeypatch.setattr(ingest, "source_paths", lambda name: [str(path)])
    result = ingest.import_new_threads(db, embed=False, sources=("openai_export",),
                                       state_path=str(tmp_path / "state.json"))
    assert result["status"] == "degraded"
    assert result["files_walked"] == 0


def test_legacy_conversation_marker_does_not_skip_missing_turns(db, tmp_path, monkeypatch):
    # The old importer stored a selection of messages under one coarse ID.
    legacy = memory("legacy excerpt")
    legacy["source_msg_id"] = "gpt:already-seen"
    write_memories(db, [legacy], apply=True)
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([{"id": "already-seen", "mapping": {
        "new-message": {"message": {"author": {"role": "user"}, "create_time": 100,
                                   "content": {"parts": ["previously missing conversation text"]}}}
    }}]), encoding="utf-8")
    monkeypatch.setattr(ingest, "source_paths", lambda name: [str(path)])
    state = str(tmp_path / "state.json")
    first = ingest.import_new_threads(db, embed=False, sources=("openai_export",),state_path=state)
    assert first["written"] == 1
    assert db.execute("SELECT raw_text FROM memories WHERE source_msg_id='oa:already-seen:new-message'").fetchone()
    assert ingest.import_new_threads(db, embed=False, sources=("openai_export",),state_path=state)["written"] == 0


def test_archive_preserves_all_branches_and_non_text_metadata(db, tmp_path):
    import zlib

    from cama.ingest.transcript_archive import archive_openai_export
    data = {"id": "history", "mapping": {"alternate": {"message": {"content": {
        "parts": [{"asset_pointer": "local-image-reference"}, "original text"]}}}}, "current_node": "other"}
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([data]), encoding="utf-8")
    assert archive_openai_export(db, str(path))["snapshots_added"] == 1
    assert archive_openai_export(db, str(path))["snapshots_added"] == 0
    raw = db.execute("SELECT payload_zlib FROM transcript_archives").fetchone()[0]
    assert json.loads(zlib.decompress(raw)) == data


def test_export_archive_distinguishes_claude_from_chatgpt(db, tmp_path):
    from cama.ingest.transcript_archive import archive_openai_export
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps([{"uuid": "claude-chat", "chat_messages": []},
                                {"id": "gpt-chat", "mapping": {}}]), encoding="utf-8")
    archive_openai_export(db, str(path))
    assert set(db.execute("SELECT platform,conversation_id FROM transcript_archives")) == {
        ("claude", "claude-chat"), ("chatgpt", "gpt-chat")}


@pytest.fixture
def db(tmp_path):
    from schema_builder import init_production_memory_schema
    path = tmp_path / "memory.db"
    init_production_memory_schema(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO librarians(name,category,description,routing_keywords,created_at,updated_at) "
                 "VALUES ('projects','topic','test','[\"project\"]','2026-09-25','2026-09-25')")
    conn.commit()
    yield conn
    conn.close()


def memory(reply=""):
    return build_memory({"source_msg_id": "cx:test:user1", "session_id": "test",
                         "user_text": "my project", "assistant_text": reply,
                         "timestamp": "2026-09-25T12:00:00Z", "entrypoint": "codex"})


def test_import_tags_and_preserves_growing_transcript_as_revision(db):
    first = memory()
    complete = memory("Here is the project plan")
    assert write_memories(db, [first, first], apply=True)["written"] == 1
    result = write_memories(db, [complete, complete], apply=True)
    assert result["written"] == 1
    assert result["revisions"] == 1
    assert result["tagged"] == 1
    assert write_memories(db, [complete], apply=True)["written"] == 0
    assert db.execute("SELECT count(*) FROM librarian_membership").fetchone()[0] == 2
    assert db.execute("SELECT raw_text FROM memories ORDER BY id").fetchone()[0] == first["raw_text"]


def test_equal_size_equal_basename_files_are_not_dropped(tmp_path):
    for folder, text in (("one", "abc"), ("two", "xyz")):
        d = tmp_path / folder
        d.mkdir()
        (d / "same.json").write_text(text)
    assert len(ingest._paths(str(tmp_path / "**" / "same.json"))) == 2


def test_failed_source_is_not_checkpointed(db, tmp_path, monkeypatch):
    source = tmp_path / "broken.jsonl"
    source.write_text("x")
    monkeypatch.setattr(ingest, "source_paths", lambda name: [str(source)])
    def fail(*args):
        raise ValueError("unsupported input")
    monkeypatch.setattr(ingest, "turns_for", fail)
    state = tmp_path / "checkpoint.json"
    result = ingest.import_new_threads(db, embed=False, state_path=str(state), sources=("codex",))
    assert result["status"] == "degraded"
    assert result["files_discovered"] == 1
    assert result["files_walked"] == 0
    assert ingest.load_state(str(state)) == {}


def matrix_db(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE memory_embeddings(memory_id INTEGER PRIMARY KEY, embedding_blob BLOB, "
              "embedding_json TEXT, model TEXT, computed_at TEXT)")
    return c


def test_embedding_cache_isolates_databases_and_dimensions(tmp_path):
    a = matrix_db(tmp_path / "a.db")
    b = matrix_db(tmp_path / "b.db")
    try:
        es.store_embedding(a, 1, [1, 0], "test", "now")
        es.store_embedding(b, 1, [0, 1], "test", "now")
        assert es.top_k_semantic(a, [1, 0])[0][1] == pytest.approx(1)
        assert es.top_k_semantic(b, [1, 0])[0][1] == pytest.approx(0)
        es.store_embedding(b, 2, [1, 0, 0], "test3", "now")
        assert es.top_k_semantic(b, [1, 0, 0])[0][0] == 2
        es.store_embedding(b, 1, [1, 0], "test", "now")
        assert es.top_k_semantic(b, [1, 0])[0][1] == pytest.approx(1)
    finally:
        a.close()
        b.close()


def test_external_embedding_update_invalidates_matrix(tmp_path):
    path = tmp_path / "shared.db"
    a = matrix_db(path)
    es.store_embedding(a, 1, [1, 0], "test", "now")
    a.commit()
    try:
        assert es.top_k_semantic(a, [1, 0])[0][1] == pytest.approx(1)
        with sqlite3.connect(path) as b:
            b.execute("UPDATE memory_embeddings SET embedding_blob=? WHERE memory_id=1", (es.pack_vec([0, 1]),))
        assert es.top_k_semantic(a, [1, 0])[0][1] == pytest.approx(0)
    finally:
        a.close()


def test_persona_state_does_not_overwrite_another_assistant(fresh_db, monkeypatch):
    from mcp_sections import identity
    conn, _ = fresh_db
    conn.execute("INSERT INTO aelen_state(key,value,updated_at) VALUES ('current_focus','Claude work','2026-09-25')")
    conn.commit()
    monkeypatch.setattr(identity, "PERSONA_NAME", "Lorien")
    asyncio.run(identity.cama_update_self("current_focus", "Codex work"))
    assert conn.execute("SELECT value FROM aelen_state WHERE key='current_focus'").fetchone()[0] == "Claude work"
    result = json.loads(asyncio.run(identity.cama_check_self()))
    assert result["state"]["current_focus"]["value"] == "Codex work"


def test_journal_reports_actual_status_and_authorship(fresh_db, monkeypatch):
    from mcp_sections import continuity
    conn, _ = fresh_db
    monkeypatch.setattr(continuity, "PERSONA_NAME", "Lorien")
    monkeypatch.setattr(continuity, "_build_daily_context", lambda c: None)
    monkeypatch.setattr(continuity, "_refresh_boot_summary", lambda c: None)
    result = json.loads(asyncio.run(continuity.cama_journal_write("Audit observations")))
    row = conn.execute("SELECT status,context FROM memories WHERE id=?", (result["memory_id"],)).fetchone()
    assert result["status"] == row[0] == "provisional"
    assert json.loads(row[1])["assistant_identity"] == "lorien"
    read = json.loads(asyncio.run(continuity.cama_journal_read()))
    assert read["entries"][0]["assistant_identity"] == "lorien"


@pytest.mark.parametrize("module", ["cama_sleep", "cama_sleep_v2"])
def test_sleep_preserves_shared_prefix_and_exact_duplicate_memories(fresh_db, module):
    import importlib
    sleep = importlib.import_module(f"cama.sleep.{module}")
    conn, _ = fresh_db
    prefix = "A shared opening sentence " * 5
    for text in (prefix + "one", prefix + "two", prefix + "one"):
        conn.execute("INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,created_at,updated_at) "
                     "VALUES (?,'experience','teaching','durable','user','2026-09-25','2026-09-25')", (text,))
    conn.commit()
    result = sleep.consolidate_memories(conn)
    assert result["duplicates_expired"] == 0
    assert len(result["duplicate_candidates"]) == 1
    assert conn.execute("SELECT count(*) FROM memories WHERE status='durable'").fetchone()[0] == 3


def test_sleep_records_degraded_ingestion(fresh_db, monkeypatch):
    from cama.librarian import cama_auto_tag
    from cama.sleep import cama_sleep as sleep
    conn, mcp = fresh_db
    monkeypatch.setattr(sleep, "DB_PATH", mcp.DB_PATH)
    monkeypatch.setattr(sleep, "consolidate_memories", lambda c: dict(clusters_found=0,edges_created=0,
                        cross_temporal_hits=0,comparisons=0,duplicates_expired=0))
    monkeypatch.setattr(sleep, "refresh_daily_context", lambda c: {"days_refreshed": 0})
    monkeypatch.setattr(sleep, "refresh_boot_summary", lambda c: {})
    monkeypatch.setattr(sleep, "write_dream_entry", lambda c: None)
    monkeypatch.setattr(sleep, "decay_provisionals", lambda c: {"expired": 0})
    monkeypatch.setattr(sleep, "backfill_embeddings", lambda c: {"backfilled": 0})
    monkeypatch.setattr(sleep, "ingest_new_threads", lambda c: {"error": "archive unavailable"})
    monkeypatch.setattr(cama_auto_tag, "backfill", lambda **kwargs: {})
    assert sleep.run_sleep_cycle()["status"] == "degraded"
    assert 'degraded' in conn.execute("SELECT value FROM aelen_state WHERE key='sleep_status'").fetchone()[0]


def test_hive_broadcast_is_unread_for_each_recipient(tmp_path, monkeypatch):
    from cama.hive import cama_hive_messages as hive
    monkeypatch.setattr(hive, "DB_PATH", str(tmp_path / "hive.db"))
    hive.send_message("source", "all", "shared work")
    assert len(hive.fetch_inbox("codex")) == 1
    assert hive.fetch_inbox("codex") == []
    assert len(hive.fetch_inbox("aelen")) == 1


def test_hive_followup_by_same_sender_is_not_a_reply(tmp_path, monkeypatch):
    from cama.hive import cama_hive_messages as hive
    monkeypatch.setattr(hive, "DB_PATH", str(tmp_path / "hive.db"))
    first = hive.send_message("codex", "aelen", "initial")
    hive.send_message("codex", "aelen", "followup", parent_message_uuid=first["message_uuid"])
    assert len(hive.fetch_inbox("aelen", mark_read=False)) == 2
