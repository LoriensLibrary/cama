"""Small on-disk cache for query embeddings.

Encoding a short query is cheap, about 30 ms. Reaching the encoder is not:
importing ``sentence_transformers`` pulls in torch and costs roughly 22
seconds in a cold process, with another 1.7 seconds to load the model.
The MCP server pays that once at startup and keeps the model resident, so
it never shows up in a tool call. The SessionStart boot hook is a
different story: it is a fresh short-lived process on every session, and
its retrieval query is usually the same constant string. Measured on
2026-09-06, warm boot spent 27.4 seconds in ``embedding_query`` for a
vector that had been computed identically the session before.

Caching the vector on disk turns that cold path into a file read. Only
short texts are cached (the encoder truncates at 512 characters anyway)
and the table is capped with least-recently-used eviction, so this never
grows into a second copy of the memory store.

Every function here is best-effort. A missing directory, a locked file, a
read-only disk, or a corrupt row must never break a boot: lookups return
None and writes do nothing.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

# Texts longer than this are not cached. The local encoder truncates its
# input at 512 characters, so anything past that is both rare as a repeat
# query and misleading to key on.
MAX_TEXT_CHARS = 512

# Upper bound on cached vectors. At 384 dimensions a float32 vector is
# 1.5 KB, so the cap keeps the file under about 1 MB.
MAX_ENTRIES = 512

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_embeddings (
    model TEXT NOT NULL,
    text_sha TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (model, text_sha)
);
CREATE INDEX IF NOT EXISTS idx_qe_last_used ON query_embeddings(last_used_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_path() -> Optional[str]:
    """Where the cache file lives, or None when caching is switched off.

    ``CAMA_EMBEDDING_CACHE`` overrides the location; set it to ``off`` to
    disable caching entirely. Otherwise the file sits beside the memory
    database, so a participant-scoped CAMA gets a participant-scoped
    cache and tests pointed at a temp database get a temp cache.
    """
    override = os.environ.get("CAMA_EMBEDDING_CACHE", "").strip()
    if override.lower() in {"off", "0", "false", "none"}:
        return None
    if override:
        return override
    db = os.environ.get("CAMA_DB_PATH")
    if not db:
        try:
            from cama.core.cama_user_paths import default_db_path

            db = default_db_path()
        except Exception:
            return None
    directory = os.path.dirname(db) or "."
    return os.path.join(directory, "embedding_cache.db")


def _connect() -> Optional[sqlite3.Connection]:
    path = cache_path()
    if not path:
        return None
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        c = sqlite3.connect(path, timeout=2.0)
        c.executescript(_SCHEMA)
        return c
    except Exception:
        return None


def get(text: str, model: str) -> Optional[List[float]]:
    """Return a cached vector for this text and model, or None on any miss."""
    if not text or len(text) > MAX_TEXT_CHARS:
        return None
    c = _connect()
    if c is None:
        return None
    try:
        row = c.execute(
            "SELECT dim, vec FROM query_embeddings WHERE model=? AND text_sha=?",
            (model, _sha(text)),
        ).fetchone()
        if row is None:
            return None
        dim, blob = row[0], row[1]
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.size != dim or vec.size == 0:
            # Truncated or mis-declared row. Drop it and report a miss.
            c.execute(
                "DELETE FROM query_embeddings WHERE model=? AND text_sha=?",
                (model, _sha(text)),
            )
            c.commit()
            return None
        c.execute(
            "UPDATE query_embeddings SET last_used_at=?, hits=hits+1 "
            "WHERE model=? AND text_sha=?",
            (_now(), model, _sha(text)),
        )
        c.commit()
        return vec.tolist()
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def put(text: str, model: str, vec) -> None:
    """Store a vector. Silently does nothing if the text is too long,
    the vector is empty, or the cache cannot be written."""
    if not text or len(text) > MAX_TEXT_CHARS:
        return
    try:
        arr = np.asarray(vec, dtype=np.float32).ravel()
    except Exception:
        return
    if arr.size == 0:
        return
    c = _connect()
    if c is None:
        return
    try:
        now = _now()
        c.execute(
            "INSERT INTO query_embeddings (model, text_sha, dim, vec, created_at, "
            "last_used_at, hits) VALUES (?,?,?,?,?,?,0) "
            "ON CONFLICT(model, text_sha) DO UPDATE SET "
            "dim=excluded.dim, vec=excluded.vec, last_used_at=excluded.last_used_at",
            (model, _sha(text), int(arr.size), arr.tobytes(), now, now),
        )
        # Least-recently-used eviction, so a long-running system converges
        # on the queries it actually repeats.
        (count,) = c.execute("SELECT COUNT(*) FROM query_embeddings").fetchone()
        if count > MAX_ENTRIES:
            c.execute(
                "DELETE FROM query_embeddings WHERE rowid IN ("
                "  SELECT rowid FROM query_embeddings "
                "  ORDER BY last_used_at ASC LIMIT ?)",
                (count - MAX_ENTRIES,),
            )
        c.commit()
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass


def stats() -> dict:
    """Entry count and total hits. Useful for checking the boot path is
    actually being served from cache."""
    c = _connect()
    if c is None:
        return {"enabled": False, "entries": 0, "hits": 0, "path": None}
    try:
        row = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM query_embeddings"
        ).fetchone()
        return {
            "enabled": True,
            "entries": row[0],
            "hits": row[1],
            "path": cache_path(),
        }
    except Exception:
        return {"enabled": False, "entries": 0, "hits": 0, "path": cache_path()}
    finally:
        try:
            c.close()
        except Exception:
            pass


def clear() -> None:
    """Drop every cached vector."""
    c = _connect()
    if c is None:
        return
    try:
        c.execute("DELETE FROM query_embeddings")
        c.commit()
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass
