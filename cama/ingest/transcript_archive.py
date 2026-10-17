"""Immutable source snapshots, including branches and non-text metadata.

Retrieval memories are derived views. Retain the original conversation JSON
so an adapter omission never destroys the source needed to rebuild them.
"""
import hashlib
import json
import os
import zlib

from cama.core.time_utils import now_iso


def archive_openai_export(conn, path):
    with open(path, encoding="utf-8") as fh:
        conversations = json.load(fh)
    if not isinstance(conversations, list):
        raise ValueError("OpenAI export must contain a conversation list")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transcript_archives (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            payload_zlib BLOB NOT NULL,
            title TEXT,
            source_created_at TEXT,
            archived_at TEXT NOT NULL,
            UNIQUE(platform, conversation_id, content_sha256)
        );
        CREATE TABLE IF NOT EXISTS transcript_archive_files (
            archive_id INTEGER NOT NULL REFERENCES transcript_archives(id),
            source_path TEXT NOT NULL,
            PRIMARY KEY(archive_id, source_path)
        );
    """)
    written = 0
    seen = 0
    for conversation in conversations:
        if not isinstance(conversation, dict):
            raise ValueError("Malformed conversation in export")
        raw = json.dumps(conversation, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        platform = ("chatgpt" if isinstance(conversation.get("mapping"), dict) else
                    "claude" if isinstance(conversation.get("chat_messages"), list) else "unknown_export")
        cid = (conversation.get("conversation_id") or conversation.get("id") or
               conversation.get("uuid") or f"unidentified:{digest}")
        cur = conn.execute(
            "INSERT OR IGNORE INTO transcript_archives "
            "(platform,conversation_id,content_sha256,payload_zlib,title,source_created_at,archived_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (platform, str(cid), digest, zlib.compress(raw), conversation.get("title") or conversation.get("name"),
             str(conversation.get("create_time", "")), now_iso()),
        )
        written += cur.rowcount
        aid = conn.execute("SELECT id FROM transcript_archives WHERE platform=? "
                           "AND conversation_id=? AND content_sha256=?", (platform, str(cid), digest)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO transcript_archive_files VALUES (?,?)",
                     (aid, os.path.abspath(path)))
        seen += 1
        if seen % 20 == 0:
            conn.commit()
    conn.commit()
    return {"snapshots_added": written, "conversations_seen": seen}
