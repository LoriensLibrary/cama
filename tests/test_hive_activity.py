import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from cama.hive import activity
from cama.hive.activity_mcp import install_observer, register


@pytest.fixture
def activity_path(tmp_path, monkeypatch):
    path = tmp_path / "activity.db"
    monkeypatch.setenv("CAMA_ACTIVITY_DB", str(path))
    return path


def test_report_and_stale_scope(activity_path):
    activity.report("aelen", "one", "Checking guards", "testing", ["tests/test_memory_guards.py"])
    activity.report("codex", "two", "Reviewing activity", "working")
    snap = activity.snapshot("aelen")
    assert len(snap["reports"]) == 1
    row = snap["reports"][0]
    assert row["source"] == "self_report" and row["fresh"]
    assert not activity.snapshot(now=row["updated"] + 301)["reports"][-1]["fresh"]


def test_sessions_and_concurrent_calls_remain_distinct(activity_path):
    activity.report("aelen", "one", "First task")
    activity.report("aelen", "two", "Second task")
    activity.observe("aelen", "one", "call1", "first", "running")
    activity.observe("aelen", "one", "call2", "second", "running")
    activity.observe("aelen", "one", "call1", "first", "returned")
    snap = activity.snapshot()
    assert len(snap["reports"]) == 2
    assert {r["state"] for r in snap["tool_calls"]} == {"running", "returned"}
    later = max(r["updated"] for r in snap["tool_calls"]) + 61
    assert {r["state"] for r in activity.snapshot(now=later)["tool_calls"]} == {"stale_unknown", "returned"}


@pytest.mark.parametrize("kwargs", [
    {"participant":"bad participant"}, {"session":"../x"},
    {"phase":"invented"}, {"task":"x"*501}, {"files":["x"]*21},
])
def test_rejects_invalid_reports(activity_path, kwargs):
    args = dict(participant="aelen", session="one", task="work")
    args.update(kwargs)
    with pytest.raises(ValueError):
        activity.report(**args)


def test_observer_records_metadata_not_arguments_or_results(activity_path):
    mcp = FastMCP("test")

    @mcp.tool()
    async def sample(secret: str):
        return "private result " + secret

    install_observer(mcp, "aelen", "test")
    install_observer(mcp, "aelen", "test")
    asyncio.run(mcp._tool_manager.call_tool("sample", {"secret":"sensitive input"}))
    snap = activity.snapshot()
    assert len(snap["tool_calls"]) == 1
    assert snap["tool_calls"][0]["state"] == "returned"
    assert "sensitive" not in json.dumps(snap) and "private result" not in json.dumps(snap)


def test_observer_failure_and_cancellation(activity_path):
    mcp = FastMCP("test")

    @mcp.tool()
    async def fail():
        raise RuntimeError("private failure")

    @mcp.tool()
    async def cancel():
        raise asyncio.CancelledError()

    install_observer(mcp, "codex", "test")
    with pytest.raises(Exception):
        asyncio.run(mcp._tool_manager.call_tool("fail", {}))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(mcp._tool_manager.call_tool("cancel", {}))
    assert {r["state"] for r in activity.snapshot()["tool_calls"]} == {"raised", "cancelled"}


def test_telemetry_failure_does_not_break_tool(activity_path, monkeypatch):
    mcp = FastMCP("test")

    @mcp.tool()
    async def sample():
        return "ok"

    def fail(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(activity, "observe", fail)
    install_observer(mcp, "codex", "test")
    assert asyncio.run(mcp._tool_manager.call_tool("sample", {})) == "ok"


def test_no_identity_no_guessed_observation(activity_path, monkeypatch):
    monkeypatch.delenv("CAMA_HIVE_IDENTITY", raising=False)
    mcp = FastMCP("test")
    register(mcp)
    assert not getattr(mcp._tool_manager, "_cama_activity_installed", False)
    result = asyncio.run(mcp._tool_manager.call_tool("hive_activity_report", {
        "participant":"codex", "task":"review", "phase":"done"}))
    assert json.loads(result)["recorded"]
    assert activity.snapshot()["tool_calls"] == []
