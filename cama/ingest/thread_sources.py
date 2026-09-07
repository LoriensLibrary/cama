"""Adapters that turn each thread archive on disk into paired exchanges.

Every adapter yields the same shape, the one
``cama_import_claude_code.build_memory`` expects:

    {source_msg_id, session_id, user_text, assistant_text, timestamp,
     entrypoint}

The threads live in five places and no two agree on a format:

  claude_code   ~/.claude/projects/**/*.jsonl        (its own module)
  codex         ~/.codex/sessions/**/rollout-*.jsonl
  openai_export conversations*.json, the mapping-tree export
  lmstudio      ~/.lmstudio/conversations/**/*.json

Each adapter's real work is deciding what counts as something a person
said. That is not obvious in any of these formats, and getting it wrong
puts injected instructions or one agent's message to another into a
store that is supposed to hold a relationship.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, Iterator, Optional, Set


def _stable_id(*parts: str) -> str:
    """A dedupe key that survives a restart.

    Python's built-in hash is salted per process, so a key built from it
    changes every run and every re-import would duplicate the whole
    archive. Codex turns carry no message id of their own, so the key
    has to be derived from content.
    """
    joined = "\x1f".join(p or "" for p in parts)
    return hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------
# A rollout carries the conversation twice. `response_item` records are the
# raw model payload, including developer blocks and the whole AGENTS.md file
# injected under role "user". `event_msg` records are the interaction as it
# happened: `user_message` is what the person typed, `agent_message` is what
# came back. Reading the event stream avoids the injected material entirely.
#
# Subagent rollouts are a trap. Codex runs judge and guardian agents whose
# `user_message` is a wrapped transcript handed over for assessment, so it
# reads exactly like a person talking at length. `session_meta.thread_source`
# marks them and the whole file is skipped.


def _codex_is_subagent(meta: dict) -> bool:
    payload = meta.get("payload") or {}
    if payload.get("thread_source") and payload["thread_source"] != "user":
        return True
    source = payload.get("source")
    return isinstance(source, dict) and "subagent" in source


def codex_turns(path: str) -> Iterator[Dict]:
    session_id = os.path.basename(path).replace("rollout-", "").replace(".jsonl", "")
    pending: Optional[str] = None
    pending_ts = ""
    reply: list = []
    first = True

    def flush():
        if pending and pending.strip():
            yield_turn = {
                "source_msg_id": f"cx:{session_id}:{_stable_id(pending[:400], pending_ts)}",
                "session_id": session_id,
                "user_text": pending.strip(),
                "assistant_text": "\n\n".join(reply).strip(),
                "timestamp": pending_ts,
                "entrypoint": "codex",
            }
            return yield_turn
        return None

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            if first:
                first = False
                if rec.get("type") == "session_meta" and _codex_is_subagent(rec):
                    return  # a judge talking to itself is not a thread
            if rec.get("type") != "event_msg":
                continue
            payload = rec.get("payload") or {}
            kind = payload.get("type")
            if kind == "user_message":
                turn = flush()
                if turn:
                    yield turn
                pending = payload.get("message") or ""
                pending_ts = rec.get("timestamp") or ""
                reply = []
            elif kind == "agent_message" and pending is not None:
                text = payload.get("message") or ""
                if text.strip():
                    reply.append(text.strip())
    turn = flush()
    if turn:
        yield turn


# ---------------------------------------------------------------------------
# OpenAI conversation export
# ---------------------------------------------------------------------------
# The export is a list of conversations, each holding a `mapping` of message
# nodes keyed by message id. Nodes form a tree because of edits and
# regenerations; ordering by create_time and pairing in sequence is close
# enough for a memory store and far simpler than walking the branch the user
# happened to land on.


def _openai_text(message: dict) -> str:
    content = message.get("content") or {}
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    out = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return "\n".join(out)


def openai_export_turns(
    path: str, skip_conversation_ids: Optional[Set[str]] = None
) -> Iterator[Dict]:
    skip = skip_conversation_ids or set()
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return
    if not isinstance(data, list):
        return

    for convo in data:
        if not isinstance(convo, dict):
            continue
        cid = convo.get("conversation_id") or convo.get("id")
        if not cid or cid in skip:
            continue
        mapping = convo.get("mapping")
        if not isinstance(mapping, dict):
            continue

        rows = []
        for node_id, node in mapping.items():
            msg = (node or {}).get("message")
            if not isinstance(msg, dict):
                continue
            role = ((msg.get("author") or {}).get("role")) or msg.get("role")
            if role not in ("user", "assistant"):
                continue  # system and tool authors are machinery
            text = _openai_text(msg)
            if not text:
                continue
            rows.append((msg.get("create_time") or 0, node_id, role, text))
        rows.sort(key=lambda r: (r[0], r[1]))

        pending = None
        reply = []
        title = convo.get("title") or ""
        for create_time, node_id, role, text in rows:
            if role == "user":
                if pending:
                    yield _openai_turn(cid, pending, reply, title)
                pending = (node_id, create_time, text)
                reply = []
            elif pending:
                reply.append(text)
        if pending:
            yield _openai_turn(cid, pending, reply, title)


def _openai_turn(cid, pending, reply, title) -> Dict:
    from datetime import datetime, timezone

    node_id, create_time, text = pending
    try:
        ts = datetime.fromtimestamp(float(create_time), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        ts = ""
    return {
        "source_msg_id": f"oa:{cid}:{node_id}",
        "session_id": cid,
        "user_text": text,
        "assistant_text": "\n\n".join(reply).strip(),
        "timestamp": ts,
        "entrypoint": f"openai-export {title}".strip(),
    }


# ---------------------------------------------------------------------------
# LM Studio
# ---------------------------------------------------------------------------
# Local-model threads. The file is one conversation object; message content
# arrives as a list of typed blocks and only the text ones matter.


def _lmstudio_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") in ("text", None):
            text = block.get("text") or ""
            if text.strip():
                parts.append(text.strip())
        elif isinstance(block, str) and block.strip():
            parts.append(block.strip())
    return "\n".join(parts)


def _lmstudio_timestamp(path: str, data: dict) -> str:
    """When the conversation happened.

    LM Studio does not stamp individual messages, and an empty timestamp
    makes the importer fall back to now, which would file threads from
    August 2025 as if they happened today. The filename is epoch
    milliseconds, and the file's own mtime is the last resort.
    """
    from datetime import datetime, timezone

    candidates = [data.get("createdAt"), data.get("created_at")]
    stem = os.path.splitext(os.path.basename(path))[0].split(".")[0]
    if stem.isdigit():
        candidates.append(int(stem))
    for value in candidates:
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            continue
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
    except OSError:
        return ""


def lmstudio_turns(path: str) -> Iterator[Dict]:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return
    if not isinstance(data, dict):
        return
    session_id = data.get("name") or os.path.splitext(os.path.basename(path))[0]
    timestamp = _lmstudio_timestamp(path, data)
    messages = data.get("messages")
    if not isinstance(messages, list):
        return

    pending = None
    reply = []
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        # LM Studio nests the actual turn under `versions` in newer files.
        if "versions" in msg and isinstance(msg.get("versions"), list) and msg["versions"]:
            inner = msg["versions"][msg.get("currentlySelected", 0) if
                                    isinstance(msg.get("currentlySelected"), int) else 0]
            if isinstance(inner, dict):
                msg = {**inner, "role": msg.get("role", inner.get("role"))}
        role = msg.get("role")
        text = _lmstudio_text(msg)
        if not text:
            continue
        if role == "user":
            if pending:
                yield _lmstudio_turn(session_id, pending, reply, timestamp)
            pending = (index, text)
            reply = []
        elif role == "assistant" and pending:
            reply.append(text)
    if pending:
        yield _lmstudio_turn(session_id, pending, reply, timestamp)


def _lmstudio_turn(session_id, pending, reply, timestamp="") -> Dict:
    index, text = pending
    return {
        "source_msg_id": f"lm:{session_id}:{index}",
        "session_id": str(session_id),
        "user_text": text,
        "assistant_text": "\n\n".join(reply).strip(),
        "timestamp": timestamp,
        "entrypoint": "lmstudio",
    }


def imported_conversation_ids(conn) -> Set[str]:
    """Conversation ids the March import already covered.

    Those rows carry ``source_msg_id`` as ``gpt:<conversation_id>``, one key
    per conversation rather than per message, so the set is only useful for
    skipping whole conversations. That is exactly what is needed: a
    conversation already in the store should not be walked again.
    """
    rows = conn.execute(
        "SELECT DISTINCT source_msg_id FROM memories "
        "WHERE source_msg_id LIKE 'gpt:%'"
    ).fetchall()
    return {r[0][4:] for r in rows if r[0]}
