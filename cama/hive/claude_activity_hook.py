"""Claude Code command hook: retain only session, tool, event, and timestamp.

No payload is echoed, logged, executed, or stored. Errors never block Claude.
The installer copies this and activity.py to a stable directory outside Git.
"""
import hashlib
import json
import sys

if __package__:
    from cama.hive import activity
else:
    import activity


def record(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected hook object")
    event = payload.get("hook_event_name")
    session_id = activity._key(payload.get("session_id"))
    session = "claude-code-" + session_id
    agent = payload.get("agent_id")
    if agent:
        session += ":agent-" + hashlib.sha256(str(agent).encode()).hexdigest()[:12]
    activity._key(session)
    states = {"PreToolUse":"requested", "PostToolUse":"returned", "PostToolUseFailure":"raised"}
    if event in states:
        tool = activity._key(payload.get("tool_name"))
        use_id = activity._key(payload.get("tool_use_id"))
        state = "cancelled" if event == "PostToolUseFailure" and payload.get("is_interrupt") is True else states[event]
        call_id = hashlib.sha256((session + ":" + use_id).encode()).hexdigest()
    elif event in {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}:
        tool, state = "event." + event, "returned"
        # Session lifecycle has no tool_use_id; latest instance of each event is enough.
        call_id = hashlib.sha256((session + ":" + event).encode()).hexdigest()
    else:
        return
    activity.observe("aelen", session, call_id, tool, state, source="claude_code_hook")


def main():
    try:
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Oversized event")
        record(json.loads(raw.decode("utf-8-sig")))
    except Exception:
        # Do not leak exception text: parser errors can contain payload fragments.
        print("CAMA activity hook could not record this event", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
