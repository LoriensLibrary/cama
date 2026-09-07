"""Import Claude Code session transcripts into CAMA as exchanges.

The companion to ``cama_import.py``, which reads a platform conversation
export. Claude Code keeps its own transcripts as JSONL under
``~/.claude/projects/<slug>/<session-id>.jsonl``, one record per line,
and those threads never made it into the library. This imports them the
same way the platform corpus was imported: every real turn, not a
selected highlight.

What counts as a turn. A transcript line is a real human message only
when ``origin.kind == "human"``. The same ``type: "user"`` envelope also
carries tool results, which are machinery rather than anything anyone
said. ``isSidechain`` marks subagent traffic, which is one assistant
talking to another, and ``isMeta`` marks context the harness injected.
Both are excluded. Each human turn is paired with the assistant text
that follows it, up to the next human turn, and stored as one exchange
in the same ``[USER] ... [ASSISTANT] ...`` shape ``cama_store_exchange``
writes.

What is left out by default. Thinking blocks are the assistant's
internal reasoning rather than what was said out loud; pass
``--include-thinking`` to keep them. Tool calls and their results are
never imported: they are the transcript of a machine doing work, and
they carry file contents and command output that have no business
sitting in a memory store.

Idempotent. Every row records ``source_msg_id`` as
``cc:<session-id>:<message-uuid>``, and a turn already present is
skipped, so re-running after new sessions accumulate imports only what
is new.

Dry run is the default. Writing to a real CAMA database requires
``--apply``, because that database is somebody's memory.

Usage:
  python -m cama.ingest.cama_import_claude_code              # dry run, default dir
  python -m cama.ingest.cama_import_claude_code --apply
  python -m cama.ingest.cama_import_claude_code --since 2026-08-01 --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional

from cama.ingest.cama_import import (
    detect_emotions,
    detect_memory_type,
    estimate_arousal,
    estimate_valence,
)

DEFAULT_TRANSCRIPT_DIR = os.path.expanduser("~/.claude/projects")
DEFAULT_DB = os.path.expanduser("~/.cama/memory.db")

# Records the harness writes into the user stream that nobody typed.
_META_KEYS = ("isMeta", "isCompactSummary")


def _is_human_turn(rec: dict) -> bool:
    """True only for a message a person actually typed.

    ``type: "user"`` is also the envelope for tool results, so the
    discriminator is ``origin.kind``. Older transcripts predate the
    ``origin`` field; there a plain string content with no tool result
    attached is the best available signal.
    """
    if rec.get("type") != "user":
        return False
    if rec.get("isSidechain"):
        return False
    if any(rec.get(k) for k in _META_KEYS):
        return False
    if rec.get("toolUseResult") is not None:
        return False
    origin = rec.get("origin")
    if isinstance(origin, dict) and "kind" in origin:
        return origin.get("kind") == "human"
    content = (rec.get("message") or {}).get("content")
    return isinstance(content, str) and bool(content.strip())


def _human_text(rec: dict) -> str:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def _assistant_text(rec: dict, include_thinking: bool = False) -> str:
    """The assistant's spoken text. Tool calls are never included."""
    if rec.get("type") != "assistant" or rec.get("isSidechain"):
        return ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(block.get("text", ""))
        elif kind == "thinking" and include_thinking:
            thought = block.get("thinking") or block.get("text") or ""
            if thought:
                parts.append(f"[THINKING] {thought}")
    return "\n".join(p for p in parts if p).strip()


def _read_records(path: str) -> Iterator[dict]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # a partially flushed final line is normal
            if isinstance(rec, dict):
                yield rec


def extract_turns(path: str, include_thinking: bool = False) -> List[Dict]:
    """Pair each human turn in one transcript with the reply that followed."""
    turns: List[Dict] = []
    pending: Optional[dict] = None
    reply: List[str] = []

    def flush():
        if pending is None:
            return
        user_text = _human_text(pending)
        if not user_text:
            return
        session_id = pending.get("sessionId") or os.path.splitext(
            os.path.basename(path)
        )[0]
        turns.append({
            "source_msg_id": f"cc:{session_id}:{pending.get('uuid')}",
            "session_id": session_id,
            "user_text": user_text,
            "assistant_text": "\n\n".join(reply).strip(),
            "timestamp": pending.get("timestamp") or "",
            "entrypoint": pending.get("entrypoint") or "claude-code",
            "cwd": pending.get("cwd") or "",
        })

    for rec in _read_records(path):
        if _is_human_turn(rec):
            flush()
            pending = rec
            reply = []
        elif pending is not None:
            text = _assistant_text(rec, include_thinking)
            if text:
                reply.append(text)
    flush()
    return turns


def _iso(ts: str) -> str:
    """Transcript timestamps are Z-suffixed; the store uses offsets."""
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def build_memory(turn: Dict) -> Dict:
    """Turn a paired exchange into the row CAMA stores."""
    raw = f"[USER] {turn['user_text']}"
    if turn["assistant_text"]:
        raw += f"\n[ASSISTANT] {turn['assistant_text']}"
    emotions = detect_emotions(raw)
    created = _iso(turn["timestamp"])
    return {
        "raw_text": raw,
        "memory_type": detect_memory_type(raw),
        "context": f"[thread:{turn['session_id']}] {turn['entrypoint']}",
        "source_type": "exchange",
        "status": "durable",
        "proposed_by": "system",
        "source_msg_id": turn["source_msg_id"],
        "consent_level": "low",
        "created_at": created,
        "updated_at": created,
        "emotions": emotions,
        "valence": estimate_valence(emotions),
        "arousal": estimate_arousal(emotions),
    }


def existing_source_ids(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT source_msg_id FROM memories "
        "WHERE source_msg_id LIKE 'cc:%'"
    ).fetchall()
    return {r[0] for r in rows}


def _encoder():
    """The local encoder, or None. Imported lazily: loading it costs about
    22 seconds and an import that is not embedding should not pay that."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        return None


def write_memories(
    conn: sqlite3.Connection,
    memories: List[Dict],
    apply: bool = False,
    embed: bool = False,
    encoder=None,
) -> Dict:
    """Insert new exchanges. Without ``apply`` nothing is written.

    ``embed`` computes each new row's vector during the import. It is
    worth the wait: blended retrieval shortlists 500 candidates ordered
    by recency, so a batch of fresh unembedded rows takes over that
    shortlist and scores zero on the semantic term, pushing out older
    memories that would otherwise have matched. The sleep daemon's
    backfill gets there eventually at 25 rows a cycle, and until it does
    retrieval is worse than before the import.
    """
    seen = existing_source_ids(conn)
    fresh = [m for m in memories if m["source_msg_id"] not in seen]
    skipped = len(memories) - len(fresh)
    if not apply:
        return {"written": 0, "would_write": len(fresh), "skipped": skipped,
                "embedded": 0}

    # A caller that already holds a loaded model (the sleep daemon) passes it
    # in; otherwise load one only when there is something to embed.
    model = encoder if encoder is not None else (_encoder() if embed and fresh else None)
    embedded = 0
    written = 0
    for m in fresh:
        cur = conn.execute(
            "INSERT INTO memories (raw_text, memory_type, context, source_type, "
            "status, proposed_by, evidence, confidence, consent_level, is_core, "
            "source_msg_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,'[]',1.0,?,0,?,?,?)",
            (m["raw_text"], m["memory_type"], m["context"], m["source_type"],
             m["status"], m["proposed_by"], m["consent_level"],
             m["source_msg_id"], m["created_at"], m["updated_at"]),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO memory_affect (memory_id, valence, arousal, "
            "dominance, emotion_json, confidence, computed_at, model) "
            "VALUES (?,?,?,0.0,?,0.6,?,'imported')",
            (mid, m["valence"], m["arousal"], json.dumps(m["emotions"]),
             m["created_at"]),
        )
        if model is not None:
            try:
                from cama.core import embedding_store as _emb_store

                vec = model.encode(m["raw_text"][:512], normalize_embeddings=True,
                                   show_progress_bar=False).tolist()
                _emb_store.store_embedding(conn, mid, vec, "all-MiniLM-L6-v2",
                                           m["created_at"])
                embedded += 1
            except Exception:
                pass  # an un-embedded row is still a stored row
        written += 1
        # Commit in batches. The live database is shared with a running MCP
        # server and the sleep daemon; one transaction held open across a
        # multi-minute embedding loop would block their writes past their
        # busy timeout.
        if written % 50 == 0:
            conn.commit()
    conn.commit()
    return {"written": written, "would_write": 0, "skipped": skipped,
            "embedded": embedded}


def collect(
    transcript_dir: str,
    include_thinking: bool = False,
    since: Optional[str] = None,
) -> List[Dict]:
    """Every importable exchange under a transcript directory."""
    paths = sorted(glob.glob(os.path.join(transcript_dir, "**", "*.jsonl"),
                             recursive=True))
    memories: List[Dict] = []
    for path in paths:
        for turn in extract_turns(path, include_thinking):
            if since and turn["timestamp"][:10] < since:
                continue
            memories.append(build_memory(turn))
    return memories


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Claude Code session transcripts into CAMA."
    )
    parser.add_argument("--transcripts", default=DEFAULT_TRANSCRIPT_DIR,
                        help=f"transcript directory (default: {DEFAULT_TRANSCRIPT_DIR})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"CAMA database (default: {DEFAULT_DB})")
    parser.add_argument("--apply", action="store_true",
                        help="actually write. Without it this is a dry run.")
    parser.add_argument("--embed", action="store_true",
                        help="compute embeddings during the import (recommended "
                             "with --apply; see write_memories for why)")
    parser.add_argument("--include-thinking", action="store_true",
                        help="also import the assistant's thinking blocks")
    parser.add_argument("--since", default=None,
                        help="only turns on or after this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of exchanges considered")
    args = parser.parse_args()

    memories = collect(args.transcripts, args.include_thinking, args.since)
    if args.limit:
        memories = memories[:args.limit]

    if not os.path.exists(args.db):
        print(f"No database at {args.db}")
        return

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        result = write_memories(conn, memories, apply=args.apply,
                                embed=args.embed)
    finally:
        conn.close()

    sessions = {m["context"] for m in memories}
    dates = sorted(m["created_at"][:10] for m in memories)
    print(f"transcripts : {args.transcripts}")
    print(f"database    : {args.db}")
    print(f"sessions    : {len(sessions)}")
    print(f"exchanges   : {len(memories)}")
    if dates:
        print(f"date range  : {dates[0]} to {dates[-1]}")
    print(f"already in  : {result['skipped']}")
    if args.apply:
        print(f"WRITTEN     : {result['written']}")
        print(f"embedded    : {result['embedded']}")
    else:
        print(f"would write : {result['would_write']}")
        print("\nDry run. Nothing was written. Add --apply to import.")


if __name__ == "__main__":
    main()
