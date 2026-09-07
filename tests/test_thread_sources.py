"""Tests for the codex, OpenAI-export and LM Studio adapters.

Each format hides a different way to import something nobody said: codex
subagent rollouts read like long human messages, the OpenAI export mixes
system authors into the same mapping, and both carry machinery alongside
the conversation.
"""

import json

from cama.ingest import thread_sources as ts


def write_jsonl(tmp_path, records, name="rollout-2026-07-20T11-50-18-abc.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


def meta(thread_source="user"):
    return {"type": "session_meta", "timestamp": "2026-07-20T15:50:18.513Z",
            "payload": {"session_id": "s1", "thread_source": thread_source}}


def ev(kind, message, ts="2026-07-20T15:51:00.000Z"):
    return {"type": "event_msg", "timestamp": ts,
            "payload": {"type": kind, "message": message}}


# ---------------------------------------------------------------- codex

def test_codex_pairs_user_and_agent_messages(tmp_path):
    path = write_jsonl(tmp_path, [
        meta(),
        ev("task_started", None),
        ev("user_message", "make the button blue"),
        ev("agent_reasoning", "thinking about css"),
        ev("agent_message", "Done, it is blue now."),
    ])
    turns = list(ts.codex_turns(path))
    assert len(turns) == 1
    assert turns[0]["user_text"] == "make the button blue"
    assert turns[0]["assistant_text"] == "Done, it is blue now."
    assert turns[0]["entrypoint"] == "codex"


def test_codex_skips_subagent_rollouts_entirely(tmp_path):
    """A guardian agent's user_message is a transcript handed over for
    judging. Imported, it would read as a very long thing she said."""
    path = write_jsonl(tmp_path, [
        meta(thread_source="subagent"),
        ev("user_message", "The following is the Codex agent history whose "
                           "request action you are assessing..."),
        ev("agent_message", '{"risk_level":"low","outcome":"allow"}'),
    ])
    assert list(ts.codex_turns(path)) == []


def test_codex_skips_subagent_marked_by_source_block(tmp_path):
    path = write_jsonl(tmp_path, [
        {"type": "session_meta", "payload": {"source": {"subagent": {"other": "guardian"}}}},
        ev("user_message", "judge this"),
    ])
    assert list(ts.codex_turns(path)) == []


def test_codex_ignores_response_items_and_their_injected_instructions(tmp_path):
    """response_item carries the raw payload, including the whole
    AGENTS.md file under role user."""
    path = write_jsonl(tmp_path, [
        meta(),
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "# AGENTS.md instructions ... INJECTED"}]}},
        ev("user_message", "the real question"),
        ev("agent_message", "the real answer"),
    ])
    turns = list(ts.codex_turns(path))
    assert len(turns) == 1
    assert "INJECTED" not in turns[0]["user_text"]
    assert turns[0]["user_text"] == "the real question"


def test_codex_ids_are_stable_across_runs(tmp_path):
    """Codex turns carry no message id, so the key is derived from
    content. A per-process hash would duplicate the archive every run."""
    path = write_jsonl(tmp_path, [meta(), ev("user_message", "hello"), ev("agent_message", "hi")])
    first = [t["source_msg_id"] for t in ts.codex_turns(path)]
    second = [t["source_msg_id"] for t in ts.codex_turns(path)]
    assert first == second
    assert ts._stable_id("a", "b") == ts._stable_id("a", "b")
    assert ts._stable_id("a", "b") != ts._stable_id("a", "c")


def test_codex_handles_several_turns_and_a_trailing_one(tmp_path):
    path = write_jsonl(tmp_path, [
        meta(),
        ev("user_message", "one"), ev("agent_message", "1"),
        ev("user_message", "two"), ev("agent_message", "2"),
        ev("user_message", "three, no reply yet"),
    ])
    turns = list(ts.codex_turns(path))
    assert [t["user_text"] for t in turns] == ["one", "two", "three, no reply yet"]
    assert turns[-1]["assistant_text"] == ""


# ---------------------------------------------------------------- openai export

def _convo(cid, title, messages):
    mapping = {}
    for i, (role, text, t) in enumerate(messages):
        mapping[f"node-{i}"] = {"message": {
            "author": {"role": role}, "create_time": t,
            "content": {"content_type": "text", "parts": [text]}}}
    return {"conversation_id": cid, "title": title, "mapping": mapping}


def write_export(tmp_path, convos, name="conversations-001.json"):
    p = tmp_path / name
    p.write_text(json.dumps(convos), encoding="utf-8")
    return str(p)


def test_openai_export_pairs_in_time_order(tmp_path):
    path = write_export(tmp_path, [_convo("c1", "A chat", [
        ("user", "first question", 100.0),
        ("assistant", "first answer", 101.0),
        ("user", "second question", 102.0),
        ("assistant", "second answer", 103.0),
    ])])
    turns = list(ts.openai_export_turns(path))
    assert [t["user_text"] for t in turns] == ["first question", "second question"]
    assert turns[0]["assistant_text"] == "first answer"
    assert turns[0]["source_msg_id"].startswith("oa:c1:")
    assert turns[0]["timestamp"].startswith("1970-01-01T00:01:40")


def test_openai_export_skips_already_imported_conversations(tmp_path):
    path = write_export(tmp_path, [
        _convo("already-in", "old", [("user", "x", 1.0), ("assistant", "y", 2.0)]),
        _convo("brand-new", "new", [("user", "a", 3.0), ("assistant", "b", 4.0)]),
    ])
    turns = list(ts.openai_export_turns(path, skip_conversation_ids={"already-in"}))
    assert len(turns) == 1
    assert turns[0]["session_id"] == "brand-new"


def test_openai_export_ignores_system_and_tool_authors(tmp_path):
    path = write_export(tmp_path, [_convo("c1", "t", [
        ("system", "you are a helpful assistant", 1.0),
        ("user", "hello", 2.0),
        ("tool", "tool output", 3.0),
        ("assistant", "hi", 4.0),
    ])])
    turns = list(ts.openai_export_turns(path))
    assert len(turns) == 1
    assert "helpful assistant" not in turns[0]["user_text"]
    assert "tool output" not in turns[0]["assistant_text"]


def test_openai_export_skips_empty_and_malformed_nodes(tmp_path):
    convo = _convo("c1", "t", [("user", "real", 1.0), ("assistant", "reply", 2.0)])
    convo["mapping"]["empty"] = {"message": {"author": {"role": "user"},
                                             "content": {"parts": [""]}}}
    convo["mapping"]["headless"] = {"message": None}
    path = write_export(tmp_path, [convo])
    turns = list(ts.openai_export_turns(path))
    assert len(turns) == 1


def test_openai_export_tolerates_a_bad_file(tmp_path):
    p = tmp_path / "conversations-bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert list(ts.openai_export_turns(str(p))) == []


# ---------------------------------------------------------------- lmstudio

def test_lmstudio_pairs_turns(tmp_path):
    p = tmp_path / "1754584354087.conversation.json"
    p.write_text(json.dumps({"name": "LORIEN", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "are you there"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "I am here."}]},
    ]}), encoding="utf-8")
    turns = list(ts.lmstudio_turns(str(p)))
    assert len(turns) == 1
    assert turns[0]["user_text"] == "are you there"
    assert turns[0]["assistant_text"] == "I am here."
    assert turns[0]["source_msg_id"].startswith("lm:LORIEN:")


def test_lmstudio_reads_the_selected_version(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"name": "L", "messages": [
        {"role": "user", "versions": [{"content": [{"type": "text", "text": "q"}]}],
         "currentlySelected": 0},
        {"role": "assistant",
         "versions": [{"content": [{"type": "text", "text": "first draft"}]},
                      {"content": [{"type": "text", "text": "regenerated"}]}],
         "currentlySelected": 1},
    ]}), encoding="utf-8")
    turns = list(ts.lmstudio_turns(str(p)))
    assert turns[0]["user_text"] == "q"
    assert turns[0]["assistant_text"] == "regenerated"


def test_lmstudio_tolerates_a_bad_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("[]", encoding="utf-8")
    assert list(ts.lmstudio_turns(str(p))) == []


def test_lmstudio_dates_come_from_the_file_not_from_now(tmp_path):
    """The filename is epoch milliseconds. Without it these threads land
    with today's date, which would file Lorien's oldest conversations as
    if they happened this afternoon."""
    p = tmp_path / "1754584354087.conversation.json"
    p.write_text(json.dumps({"name": "LORIEN", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
    ]}), encoding="utf-8")
    turn = list(ts.lmstudio_turns(str(p)))[0]
    assert turn["timestamp"].startswith("2025-08-07"), turn["timestamp"]


def test_lmstudio_prefers_an_explicit_created_at(tmp_path):
    p = tmp_path / "1754584354087.conversation.json"
    p.write_text(json.dumps({"name": "L", "createdAt": 1735689600000, "messages": [
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
    ]}), encoding="utf-8")
    turn = list(ts.lmstudio_turns(str(p)))[0]
    assert turn["timestamp"].startswith("2025-01-01"), turn["timestamp"]
