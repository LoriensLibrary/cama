"""Float32 blob embedding storage for CAMA.

Embeddings live in memory_embeddings. Historically stored as JSON text
(embedding_json); as of the 2026-08 migration they are packed little-endian
float32 blobs (embedding_blob), ~4x smaller on disk and directly scorable
with numpy. Readers coalesce blob-first with JSON fallback so a
half-migrated DB keeps working; writers write blob only.

Depends only on numpy + stdlib so any layer (MCP server, sleep scripts,
librarian, one-off tools) can import it without circular imports.
"""

import json
import sqlite3
from typing import Dict, Optional, Sequence

import numpy as np

EMB_DTYPE = "<f4"  # little-endian float32, the on-disk blob format

# SQLite's default max host parameters is 999; stay under it when chunking
_IN_CHUNK = 900


def pack_vec(vec) -> bytes:
    return np.asarray(vec, dtype=EMB_DTYPE).tobytes()


def unpack_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=EMB_DTYPE)


def vec_from_row(row) -> Optional[np.ndarray]:
    """Read one memory_embeddings row: blob first, JSON fallback."""
    try:
        blob = row["embedding_blob"]
    except (IndexError, KeyError):
        blob = None
    if blob:
        return unpack_vec(blob)
    try:
        ej = row["embedding_json"]
    except (IndexError, KeyError):
        return None
    if ej:
        try:
            v = json.loads(ej)
            if isinstance(v, list) and v:
                return np.asarray(v, dtype=EMB_DTYPE)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def ensure_blob_column(c) -> None:
    try:
        c.execute("ALTER TABLE memory_embeddings ADD COLUMN embedding_blob BLOB")
    except sqlite3.OperationalError:
        pass  # column already exists


def store_embedding(c, memory_id: int, vec, model: str, computed_at: str) -> None:
    """Write an embedding as a float32 blob (embedding_json stays NULL)."""
    c.execute(
        "INSERT OR REPLACE INTO memory_embeddings "
        "(memory_id, embedding_blob, embedding_json, model, computed_at) "
        "VALUES (?,?,NULL,?,?)",
        (memory_id, pack_vec(vec), model, computed_at),
    )


def fetch_emb_map(c, mids: Sequence[int]) -> Dict[int, np.ndarray]:
    """Batch-fetch embeddings for the given memory ids."""
    out: Dict[int, np.ndarray] = {}
    mids = list(mids)
    for i in range(0, len(mids), _IN_CHUNK):
        chunk = mids[i : i + _IN_CHUNK]
        ph = ",".join("?" * len(chunk))
        for r in c.execute(
            f"SELECT memory_id, embedding_blob, embedding_json "
            f"FROM memory_embeddings WHERE memory_id IN ({ph})",
            chunk,
        ):
            v = vec_from_row(r)
            if v is not None and v.size:
                out[r["memory_id"]] = v
    return out


def cosine(a, b) -> float:
    """Scalar cosine similarity; 0.0 on None, empty, or shape mismatch."""
    if a is None or b is None:
        return 0.0
    av = np.asarray(a, dtype=np.float32)
    bv = np.asarray(b, dtype=np.float32)
    if av.size == 0 or bv.size == 0 or av.shape != bv.shape:
        return 0.0
    denom = float(np.linalg.norm(av)) * float(np.linalg.norm(bv))
    if denom == 0.0:
        return 0.0
    return float(av @ bv) / denom


def cosine_scores(query_vec, emb_map: Dict[int, np.ndarray]) -> Dict[int, float]:
    """Batched cosine similarity of one query vector against a fetched map.

    Vectors whose dimension differs from the query's score as absent
    (mirrors the old _cosine_sim contract of returning 0.0 on mismatch).
    """
    if query_vec is None or not emb_map:
        return {}
    q = np.asarray(query_vec, dtype=np.float32)
    if q.size == 0:
        return {}
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return {}
    ids = [mid for mid, v in emb_map.items() if v.shape == q.shape]
    if not ids:
        return {}
    mat = np.stack([emb_map[mid] for mid in ids]).astype(np.float32, copy=False)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0.0] = 1.0
    sims = (mat @ q) / (norms * qn)
    return {mid: float(s) for mid, s in zip(ids, sims)}
