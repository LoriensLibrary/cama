"""Regression coverage for the notification sink and remote Hive boundaries."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import types

import pytest
from starlette.testclient import TestClient

import cama_hive_mcp as remote
from cama.hive import cama_hive_messages as messages
from cama.hive import cama_hive_security as security
from cama.hive import cama_hive_watcher as watcher


@pytest.fixture
def isolated_hive(tmp_path, monkeypatch):
    monkeypatch.setattr(messages, "DB_PATH", str(tmp_path / "messages.db"))
    monkeypatch.setattr(remote, "IDENTITY", "codex")
    security._request_log.clear()
    messages.init_messages_tables()
    yield
    security._request_log.clear()


@pytest.mark.parametrize("payload", [
    '$(Write-Output INJECTION_SENTINEL)',
    '\"); Write-Output INJECTION_SENTINEL; #',
    'quotes " apostrophe \' backtick ` newline\n<xml>& 🐝',
])
def test_toast_content_is_data_not_powershell_source(monkeypatch, payload):
    monkeypatch.setitem(sys.modules, "plyer", None)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    watcher.notify_windows(payload, payload)
    args, kwargs = calls[0]
    assert payload not in args[0][-1]
    assert args[0][-1] == watcher._TOAST_SCRIPT
    assert "-NoProfile" in args[0] and "-NonInteractive" in args[0]
    assert json.loads(kwargs["input"]) == {"title": payload, "message": payload}
    assert kwargs.get("shell", False) is False
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_plyer_notifications_do_not_launch_powershell(monkeypatch):
    received = []
    monkeypatch.setitem(sys.modules, "plyer", types.SimpleNamespace(
        notification=types.SimpleNamespace(notify=lambda **kwargs: received.append(kwargs)),
    ))
    def unexpected(*args, **kwargs):
        pytest.fail("PowerShell should not run when plyer is available")
    monkeypatch.setattr(subprocess, "run", unexpected)
    watcher.notify_windows("Hive", "hello")
    assert received[0]["message"] == "hello"


def test_signal_poll_preserves_payload_without_executing_it(tmp_path, monkeypatch):
    db = tmp_path / "watcher.db"
    payload = '$(Write-Output INJECTION_SENTINEL)'
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE hive_pheromones (id INTEGER, pheromone_type TEXT, signal TEXT, "
                     "intensity REAL, source_thread TEXT, source_context TEXT, is_active INTEGER)")
        conn.execute("INSERT INTO hive_pheromones VALUES (1, 'warning', ?, .5, 'api:codex', '', 1)",
                     (payload,))
    monkeypatch.setattr(watcher, "DB_PATH", str(db))
    monkeypatch.setattr(watcher, "CAMA_DIR", str(tmp_path))
    monkeypatch.setattr(watcher, "ALERT_LOG", str(tmp_path / "alerts.log"))
    monkeypatch.setattr(watcher, "last_seen_id", 0)
    monkeypatch.setitem(sys.modules, "plyer", None)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert watcher.check_for_new_signals() == 1
    assert watcher.check_for_new_signals() == 0
    assert payload not in calls[0][0][0][-1]
    assert payload in json.loads(calls[0][1]["input"])["message"]
    assert json.loads((tmp_path / ".hive_pending_for_aelen").read_text())["signal"] == payload


def test_toast_failure_does_not_crash_watcher(monkeypatch):
    monkeypatch.setitem(sys.modules, "plyer", None)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("powershell", 5)
    monkeypatch.setattr(subprocess, "run", timeout)
    watcher.notify_windows("Hive", "hello")


@pytest.mark.parametrize("secret", ["", "short", "x" * 31, "x" * 257, "x" * 32 + "/extra"])
def test_http_refuses_missing_weak_or_invalid_secret(monkeypatch, secret):
    monkeypatch.setattr(remote, "PATH_SECRET", secret)
    with pytest.raises(SystemExit, match="Refusing to listen"):
        remote._banner("http")


def test_http_banner_redacts_credential_and_stdio_needs_none(monkeypatch, capsys):
    secret = "a_valid_random_test_credential_123456789"
    monkeypatch.setattr(remote, "PATH_SECRET", secret)
    monkeypatch.setattr(remote, "_mount", "/mcp/" + secret)
    remote._banner("http")
    assert secret not in capsys.readouterr().err
    monkeypatch.setattr(remote, "PATH_SECRET", "")
    remote._banner("stdio")


def test_http_route_requires_exact_secret_path(monkeypatch):
    secret = "test_credential_for_route_check_123456789"
    monkeypatch.setattr(remote.mcp.settings, "streamable_http_path", "/mcp/" + secret)
    app = remote.mcp.streamable_http_app()
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "security-test", "version": "1"},
    }}
    with TestClient(app) as client:
        headers = {"Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", json=request, headers=headers).status_code == 404
        assert client.post("/mcp/wrong", json=request, headers=headers).status_code == 404
        response = client.post("/mcp/" + secret, json=request, headers=headers)
        assert response.status_code == 200
        assert "result" in response.json()


def test_remote_cannot_read_or_reply_to_another_identity_thread(isolated_hive):
    private = messages.send_message("aelen", "lorien", "PRIVATE_SENTINEL")
    result = json.loads(asyncio.run(remote.hive_view_thread(private["thread_id"])))
    assert result["found"] is False
    reply = json.loads(asyncio.run(remote.hive_send_message(
        "aelen", "inject reply", parent_message_uuid=private["message_uuid"],
    )))
    assert "error" in reply
    thread = messages.view_thread(private["thread_id"])
    assert thread["count"] == 1 and thread["messages"][0]["status"] == "posted"


def test_mixed_thread_hides_private_history_in_both_read_paths(isolated_hive):
    private = messages.send_message("aelen", "lorien", "PRIVATE_SENTINEL")
    # A trusted local participant can create mixed-recipient legacy history.
    messages.send_message("aelen", "codex", "visible", parent_message_uuid=private["message_uuid"])
    inbox = json.loads(asyncio.run(remote.hive_check_inbox(mark_read=False)))
    thread = json.loads(asyncio.run(remote.hive_view_thread(private["thread_id"])))
    assert inbox["count"] == 1 and thread["count"] == 1
    assert "PRIVATE_SENTINEL" not in json.dumps(inbox)
    assert "PRIVATE_SENTINEL" not in json.dumps(thread)
    assert messages.view_thread(private["thread_id"])["count"] == 2


def test_authorized_reply_and_broadcast_remain_accessible(isolated_hive):
    parent = messages.send_message("aelen", "codex", "hello")
    reply = json.loads(asyncio.run(remote.hive_send_message(
        "aelen", "reply", parent_message_uuid=parent["message_uuid"],
    )))
    assert reply["sender"] == "codex" and reply["thread_id"] == parent["thread_id"]
    broadcast = messages.send_message("aelen", "all", "shared")
    assert json.loads(asyncio.run(remote.hive_view_thread(broadcast["thread_id"])))["count"] == 1


def test_read_only_identity_cannot_write_or_consume_inbox(isolated_hive, monkeypatch):
    monkeypatch.setattr(remote, "IDENTITY", "ember")
    messages.send_message("aelen", "ember", "read only")
    assert "error" in json.loads(asyncio.run(remote.hive_send_message("aelen", "blocked")))
    assert "error" in json.loads(asyncio.run(remote.hive_emit_pheromone("warning", "blocked")))
    assert "error" in json.loads(asyncio.run(remote.hive_check_inbox()))
    assert json.loads(asyncio.run(remote.hive_check_inbox(mark_read=False)))["count"] == 1


def test_remote_write_rate_limit(isolated_hive):
    for _ in range(security.RATE_LIMITS["write"]["burst"]):
        assert "error" not in json.loads(asyncio.run(remote.hive_send_message("aelen", "hello")))
    assert "limit exceeded" in json.loads(asyncio.run(remote.hive_send_message("aelen", "blocked")))["error"]


def test_mcp_surface_matches_documented_tools_and_pins_sender(isolated_hive):
    tools = asyncio.run(remote.mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "hive_whoami", "hive_check_inbox", "hive_send_message", "hive_view_thread",
        "hive_list_threads", "hive_read_pheromones", "hive_emit_pheromone", "hive_state",
    }
    send = next(tool for tool in tools if tool.name == "hive_send_message")
    assert "sender" not in send.inputSchema["properties"]
    result = json.loads(asyncio.run(remote.hive_send_message("aelen", "from my pinned identity")))
    assert result["sender"] == "codex"
