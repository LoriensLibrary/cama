"""One-off migration: embedding_json (TEXT) -> embedding_blob (float32 BLOB).

Cuts embedding storage ~4x (384d JSON text ~8KB/row -> 1536B/row) and lets
retrieval score with numpy instead of pure-Python loops.

Stage 1 (default), safe to run while the MCP server is up (WAL + batches):
    python migrate_embeddings_float32.py
  - adds embedding_blob column if missing
  - backfills blobs from embedding_json in batches (idempotent, resumable)
  - verifies counts and round-trip cosine on a sample

Stage 2 (--finalize), ONLY after the server has been restarted on code
that reads blobs (blob-first readers landed 2026-08):
    python migrate_embeddings_float32.py --finalize
  - re-runs backfill to catch rows the old server wrote since stage 1
  - NULLs embedding_json where a blob exists
  - VACUUMs to reclaim disk (needs the DB otherwise idle)
"""

import argparse
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cama.core import embedding_store as emb_store  # noqa: E402

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
BATCH = 2000


def open_db():
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def counts(c):
    row = c.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN embedding_blob IS NOT NULL THEN 1 ELSE 0 END) AS with_blob,
                  SUM(CASE WHEN embedding_blob IS NULL AND embedding_json IS NOT NULL THEN 1 ELSE 0 END) AS json_only,
                  SUM(CASE WHEN embedding_blob IS NULL AND embedding_json IS NULL THEN 1 ELSE 0 END) AS neither
           FROM memory_embeddings"""
    ).fetchone()
    return dict(row)


def backfill(c):
    """Convert embedding_json to float32 blobs. Never destroys a source vector.

    Pages by a keyset cursor on memory_id rather than a bare LIMIT. The earlier
    version re-selected the same rows every pass, so an unparseable one would
    spin forever; it broke the loop by NULLing embedding_json, which deleted the
    only copy of a vector precisely because it had failed to read it. Advancing
    a cursor means a bad row is passed over without being touched.

    Returns (converted, failed_ids). Failed rows keep their embedding_json.
    """
    scanned = 0
    converted = 0
    failed = []
    last_id = -1
    while True:
        rows = c.execute(
            "SELECT memory_id, embedding_json FROM memory_embeddings "
            "WHERE embedding_blob IS NULL AND embedding_json IS NOT NULL "
            "AND memory_id > ? ORDER BY memory_id LIMIT ?",
            (last_id, BATCH),
        ).fetchall()
        if not rows:
            break
        for r in rows:
            last_id = r["memory_id"]
            v = emb_store.vec_from_row(r)
            if v is None or not v.size:
                failed.append(r["memory_id"])
                continue
            c.execute(
                "UPDATE memory_embeddings SET embedding_blob=? WHERE memory_id=?",
                (emb_store.pack_vec(v), r["memory_id"]),
            )
            converted += 1
        c.commit()
        scanned += len(rows)
        print(f"  scanned {scanned}, converted {converted}...", flush=True)
    if failed:
        shown = ", ".join(str(i) for i in failed[:20])
        more = f" (+{len(failed) - 20} more)" if len(failed) > 20 else ""
        print(f"  WARNING: {len(failed)} rows have unparseable embedding_json and were "
              f"left untouched: {shown}{more}")
        print("  Their JSON is intact. To rebuild those vectors, run the sleep "
              "daemon's backfill_embeddings, which recomputes from raw_text.")
    return converted, failed


def verify(c, sample_size=50):
    rows = c.execute(
        "SELECT memory_id, embedding_blob, embedding_json FROM memory_embeddings "
        "WHERE embedding_blob IS NOT NULL AND embedding_json IS NOT NULL"
    ).fetchall()
    if not rows:
        print("  verify: no rows have both formats (nothing to compare)")
        return True
    sample = random.sample(rows, min(sample_size, len(rows)))
    worst = 1.0
    for r in sample:
        blob_vec = emb_store.unpack_vec(r["embedding_blob"])
        json_vec = emb_store.vec_from_row({"embedding_blob": None, "embedding_json": r["embedding_json"]})
        sim = emb_store.cosine(blob_vec, json_vec)
        worst = min(worst, sim)
        if sim < 0.999:
            print(f"  verify FAILED at memory_id={r['memory_id']}: cosine={sim:.6f}")
            return False
    print(f"  verify OK: {len(sample)} sampled, worst round-trip cosine {worst:.6f}")
    return True


def finalize(c):
    n = c.execute(
        "SELECT COUNT(*) AS n FROM memory_embeddings "
        "WHERE embedding_blob IS NOT NULL AND embedding_json IS NOT NULL"
    ).fetchone()["n"]
    c.execute(
        "UPDATE memory_embeddings SET embedding_json=NULL WHERE embedding_blob IS NOT NULL"
    )
    c.commit()
    print(f"  cleared embedding_json on {n} rows")
    size_before = os.path.getsize(DB_PATH)
    print("  VACUUM (this can take a few minutes on a large DB)...")
    c.execute("VACUUM")
    size_after = os.path.getsize(DB_PATH)
    print(f"  DB size: {size_before/1e6:.0f} MB -> {size_after/1e6:.0f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--finalize", action="store_true",
                    help="NULL embedding_json where blob exists, then VACUUM. "
                         "Run ONLY after the server restarted on blob-reading code.")
    args = ap.parse_args()

    print(f"DB: {DB_PATH} ({os.path.getsize(DB_PATH)/1e6:.0f} MB)")
    c = open_db()
    try:
        emb_store.ensure_blob_column(c)
        c.commit()
        print("before:", counts(c))
        done, failed = backfill(c)
        print(f"backfill complete: {done} rows converted this run"
              + (f", {len(failed)} unparseable rows skipped" if failed else ""))
        if not verify(c):
            print("ABORTING: verification failed, embedding_json left untouched")
            sys.exit(1)
        if args.finalize:
            finalize(c)
        print("after:", counts(c))
        if not args.finalize:
            print("\nStage 1 done. embedding_json kept for the still-running old server.")
            print("After restarting the MCP server on the new code, run:")
            print("  python migrate_embeddings_float32.py --finalize")
    finally:
        c.close()


if __name__ == "__main__":
    main()
