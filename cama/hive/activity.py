"""Local shared operational activity; no prompts, tool arguments or results.

CLI works without restarting MCP: python -m cama.hive.activity list
Explicit reports are self-reported. Tool observations only cover this server.
"""
import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value):
        raise ValueError("Invalid participant or session identifier")
    return value


def _connect(path=None):
    path = Path(path or os.environ.get("CAMA_ACTIVITY_DB", Path.home() / ".cama" / "hive_activity.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=5)
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE IF NOT EXISTS reports (
        participant TEXT, session TEXT, task TEXT, phase TEXT, details TEXT,
        updated REAL, PRIMARY KEY(participant,session));
      CREATE TABLE IF NOT EXISTS calls (
        call_id TEXT PRIMARY KEY, participant TEXT, session TEXT, tool TEXT,
        state TEXT, updated REAL);
      CREATE INDEX IF NOT EXISTS activity_calls_time ON calls(updated);
    ''')
    if "source" not in {r[1] for r in c.execute("PRAGMA table_info(calls)")}:
        try:
            c.execute("ALTER TABLE calls ADD COLUMN source TEXT NOT NULL DEFAULT 'observed_cama_tool'")
        except sqlite3.OperationalError:
            if "source" not in {r[1] for r in c.execute("PRAGMA table_info(calls)")}:
                c.close()
                raise
    return c


def report(participant, session, task, phase="working", files=None, result="", path=None):
    participant, session = _key(participant), _key(session)
    if phase not in {"working", "testing", "waiting", "blocked", "done"}:
        raise ValueError("Unknown phase")
    if not isinstance(task, str) or len(task) > 500 or len(result) > 1000:
        raise ValueError("Task/result exceeds limits")
    files = files or []
    if not isinstance(files, list) or len(files) > 20 or any(not isinstance(f, str) or len(f) > 300 for f in files):
        raise ValueError("At most 20 short file labels")
    c = _connect(path)
    try:
        with c:
            c.execute("INSERT INTO reports VALUES(?,?,?,?,?,?) ON CONFLICT(participant,session) "
                      "DO UPDATE SET task=excluded.task,phase=excluded.phase,details=excluded.details,updated=excluded.updated",
                      (participant, session, task, phase, json.dumps({"files": files, "result": result}), time.time()))
    finally:
        c.close()
    return {"recorded": True, "source": "self_report", "participant": participant, "session": session}


def observe(participant, session, call_id, tool, state, path=None, source="observed_cama_tool"):
    for value in (participant, session, call_id, tool):
        _key(value)
    if state not in {"requested", "running", "returned", "raised", "cancelled"}:
        raise ValueError("Unknown tool state")
    if source not in {"observed_cama_tool", "claude_code_hook"}:
        raise ValueError("Unknown observation source")
    c = _connect(path)
    try:
        with c:
            c.execute("INSERT INTO calls(call_id,participant,session,tool,state,updated,source) "
                      "VALUES(?,?,?,?,?,?,?) ON CONFLICT(call_id) "
                      "DO UPDATE SET state=excluded.state,updated=excluded.updated "
                      "WHERE calls.state IN ('requested','running') OR excluded.state NOT IN ('requested','running')",
                      (call_id, participant, session, tool, state, time.time(), source))
            # Bounded operational history, independent of personal memory.
            c.execute("DELETE FROM calls WHERE call_id IN (SELECT call_id FROM calls "
                      "WHERE state NOT IN ('requested','running') ORDER BY updated DESC LIMIT -1 OFFSET 2000)")
            c.execute("DELETE FROM calls WHERE updated < ?", (time.time() - 7*86400,))
    finally:
        c.close()


def snapshot(participant=None, path=None, now=None):
    if participant:
        _key(participant)
    now = time.time() if now is None else now
    c = _connect(path)
    try:
        filt, args = (" WHERE participant=?", (participant,)) if participant else ("", ())
        reports = [dict(r) for r in c.execute("SELECT * FROM reports" + filt + " ORDER BY updated DESC LIMIT 100", args)]
        calls = [dict(r) for r in c.execute("SELECT * FROM calls" + filt + " ORDER BY updated DESC LIMIT 100", args)]
    finally:
        c.close()
    for r in reports:
        r.update(json.loads(r.pop("details")))
        r["source"] = "self_report"
        r["age_seconds"] = max(0, round(now - r["updated"], 1))
        r["fresh"] = r["age_seconds"] <= 300
    for r in calls:
        r["age_seconds"] = max(0, round(now - r["updated"], 1))
        r["fresh"] = r["age_seconds"] <= 60
        if r["state"] in {"requested", "running"} and not r["fresh"]:
            r["state"] = "stale_unknown"
    return {"reports": reports, "tool_calls": calls,
            "scope": "Shared local CAMA status only. No activity does not prove an assistant is idle. "
                     "Returned means a tool returned, not that its result was successful. "
                     "Claude Code hook events cover its tools when connected. PreToolUse means requested, "
                     "not confirmed execution. Unsent model work and uninstrumented apps are not observed."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["list", "report"])
    p.add_argument("--participant")
    p.add_argument("--session")
    p.add_argument("--task", default="")
    p.add_argument("--phase", default="working")
    p.add_argument("--result", default="")
    a = p.parse_args()
    output = snapshot(a.participant) if a.action == "list" else report(a.participant, a.session, a.task, a.phase, result=a.result)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
