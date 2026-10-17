import json
import sqlite3
import subprocess
import sys

import pytest
from mcp.server.fastmcp import FastMCP

from cama.hive import activity
from cama.hive import claude_activity_hook as hook
from cama.hive.install_claude_activity import configure


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMA_ACTIVITY_DB", str(tmp_path / "activity.db"))
    monkeypatch.delenv("CAMA_ACTIVITY_PARTICIPANT", raising=False)


def test_identity_is_explicit(monkeypatch):
    hook.record(payload("Stop"))
    assert activity.snapshot()["tool_calls"][0]["participant"] == "claude-code"
    monkeypatch.setenv("CAMA_ACTIVITY_PARTICIPANT", "aelen")
    hook.record(payload("SessionStart"))
    assert activity.snapshot(participant="aelen")["tool_calls"][0]["participant"] == "aelen"


def payload(event, **extra):
    return dict(session_id="session-one", hook_event_name=event,
                tool_name="Bash", tool_use_id="tool-one", **extra)


def test_order_and_privacy():
    hook.record(payload("PreToolUse", tool_input={"command": "secret"}))
    assert activity.snapshot()["tool_calls"][0]["state"] == "requested"
    hook.record(payload("PostToolUse", tool_response="secret result"))
    hook.record(payload("PreToolUse"))
    rows = activity.snapshot()["tool_calls"]
    assert len(rows) == 1 and rows[0]["state"] == "returned"
    assert rows[0]["source"] == "claude_code_hook"
    assert "secret" not in json.dumps(rows)


def test_subagent_and_failure():
    hook.record(payload("PostToolUseFailure", error="private"))
    hook.record(payload("PostToolUseFailure", agent_id="child", is_interrupt=True))
    rows = activity.snapshot()["tool_calls"]
    assert len({r["session"] for r in rows}) == 2
    assert {r["state"] for r in rows} == {"raised", "cancelled"}


def test_lifecycle():
    hook.record(payload("Stop", last_assistant_message="private"))
    assert activity.snapshot()["tool_calls"][0]["tool"] == "event.Stop"


def test_legacy_schema_migration(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE calls(call_id TEXT PRIMARY KEY,participant TEXT,session TEXT,tool TEXT,state TEXT,updated REAL)")
        c.execute("INSERT INTO calls VALUES('one','aelen','session','Bash','returned',1)")
    assert activity.snapshot(path=path)["tool_calls"][0]["source"] == "observed_cama_tool"


def test_settings_preserved_and_idempotent():
    original = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "existing"}]}]}, "other": True}
    updated = configure(original, "new-command")
    assert original["hooks"]["PreToolUse"] == updated["hooks"]["PreToolUse"][:1]
    assert updated["other"] is True
    assert configure(updated, "new-command") == updated


def test_installer_replaces_old_runtime_and_deduplicates():
    old = '"C:/old/python.exe" "C:/Users/test/.claude/cama_activity/claude_activity_hook.py"'
    new = old.replace("old/python", "new/python")
    other = {"type": "command", "command": "keep-me"}
    original = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": old}, other]},
        {"hooks": [{"type": "command", "command": old}]},
    ]}}
    updated = configure(original, new)
    groups = updated["hooks"]["PreToolUse"]
    assert groups[0] == {"matcher": "Bash", "hooks": [other]}
    assert [h["command"] for g in groups for h in g["hooks"]] == ["keep-me", new]
    assert configure(updated, new) == updated


def test_local_wrapper_registers_activity(monkeypatch):
    import cama_hive_messages_mcp
    monkeypatch.delenv("CAMA_HIVE_IDENTITY", raising=False)
    mcp = FastMCP("registration")
    cama_hive_messages_mcp.register(mcp)
    assert mcp._tool_manager.get_tool("hive_activity_list") is not None
    assert mcp._tool_manager.get_tool("hive_activity_report") is not None


def test_command_hook_fails_open_and_records():
    command = [sys.executable, hook.__file__]
    bad = subprocess.run(command, input="private malformed", text=True, capture_output=True, timeout=15)
    assert bad.returncode == 0 and not bad.stdout and "private" not in bad.stderr
    good = subprocess.run(command, input=json.dumps(payload("PostToolUse")), text=True, capture_output=True, timeout=15)
    assert good.returncode == 0 and not good.stdout and not good.stderr
    assert activity.snapshot()["tool_calls"][0]["state"] == "returned"
