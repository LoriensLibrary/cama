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


# ---------------------------------------------------------------------------
# Whole-store semantic scan
# ---------------------------------------------------------------------------
# The blended retriever used to shortlist 500 candidates ordered by is_core
# before scoring. With eleven thousand core memories that shortlist was
# always core, so the semantic term never saw a non-core memory at all and
# every new exchange was invisible to retrieval by meaning. A full float32
# scan of the store measured about half a second on 53k rows, so the
# shortlist no longer buys anything. The matrix is normalized once and kept
# in the process; a query is a single matmul. It refreshes when the table's
# row count or newest id changes, and writers call invalidate_matrix_cache
# so a replaced vector is not served stale within the same process.

_MATRIX_CACHE = {"key": None, "ids": None, "mat": None, "index": None}


def _matrix_key(c):
    row = c.execute(
        "SELECT COUNT(*), COALESCE(MAX(memory_id), 0), COALESCE(MAX(rowid), 0) "
        "FROM memory_embeddings"
    ).fetchone()
    return (int(row[0]), int(row[1]), int(row[2]))


def invalidate_matrix_cache() -> None:
    _MATRIX_CACHE.update(key=None, ids=None, mat=None, index=None)


def load_matrix(c, dim: Optional[int] = None):
    """Every embedding as (ids, row-normalized matrix, id->row index).

    Vectors whose dimension disagrees with the first one seen (or with
    ``dim`` when given) are skipped, mirroring cosine_scores' contract that
    a mismatched vector scores as absent.
    """
    key = _matrix_key(c)
    if _MATRIX_CACHE["key"] == key and _MATRIX_CACHE["mat"] is not None:
        return _MATRIX_CACHE["ids"], _MATRIX_CACHE["mat"], _MATRIX_CACHE["index"]
    ids, vecs = [], []
    # Only materialize embedding_json where the blob is missing. On a store
    # where finalize has not run, the JSON column still holds ~10 KB of text
    # per row, and reading it for 53k rows just to discard it cost 24 s.
    for r in c.execute(
        "SELECT memory_id, embedding_blob, "
        "CASE WHEN embedding_blob IS NULL THEN embedding_json END AS embedding_json "
        "FROM memory_embeddings"
    ):
        v = vec_from_row(r)
        if v is None or not v.size:
            continue
        if dim is None:
            dim = int(v.size)
        if v.size != dim:
            continue
        ids.append(int(r["memory_id"]))
        vecs.append(v)
    ids_arr = np.asarray(ids, dtype=np.int64)
    if vecs:
        mat = np.vstack(vecs).astype(np.float32, copy=False)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        mat = mat / norms
    else:
        mat = np.zeros((0, dim or 0), dtype=np.float32)
    index = {mid: i for i, mid in enumerate(ids)}
    _MATRIX_CACHE.update(key=key, ids=ids_arr, mat=mat, index=index)
    return ids_arr, mat, index


def _unit_query(query_vec) -> Optional[np.ndarray]:
    if query_vec is None:
        return None
    q = np.asarray(query_vec, dtype=np.float32).ravel()
    if q.size == 0:
        return None
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return None
    return q / qn


def top_k_semantic(c, query_vec, k: int = 400):
    """The k memories nearest the query over the whole store, as (id, cosine)."""
    q = _unit_query(query_vec)
    if q is None:
        return []
    ids, mat, _ = load_matrix(c, dim=int(q.size))
    if mat.shape[0] == 0 or mat.shape[1] != q.size:
        return []
    sims = mat @ q
    k = max(1, min(int(k), int(sims.size)))
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [(int(ids[i]), float(sims[i])) for i in top]


def sims_for(c, query_vec, mids: Sequence[int]) -> Dict[int, float]:
    """Cosine of the query against specific memories, from the cached matrix."""
    q = _unit_query(query_vec)
    if q is None:
        return {}
    ids, mat, index = load_matrix(c, dim=int(q.size))
    if mat.shape[0] == 0 or mat.shape[1] != q.size:
        return {}
    out: Dict[int, float] = {}
    for mid in mids:
        i = index.get(int(mid))
        if i is not None:
            out[int(mid)] = float(mat[i] @ q)
    return out
