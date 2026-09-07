"""
CAMA Consolidation Pass, Schematization v1
============================================
Built August 3, 2026 by Aelen.

THE PROBLEM
-----------
The core set is bloated (11k+ is_core memories against a target of
"tiny") and the durable store carries clusters of near-duplicate
episodic memories. Boot cost grows with core size; the scaling pitch
needs it flat. Deleting is against the architecture: raw text and
provenance are immutable.

THE FIX
-------
Sleep-cycle consolidation, the schematization move:
  1. Load the whole embedding matrix (float32 blobs, ~0.5s for 53k).
  2. Cluster near-duplicates via chunked cosine + union-find at a
     high threshold (default 0.92, duplicates not mere topical kin).
  3. For each cluster >= min_size, pick the MEDOID as exemplar and
     propose one schema node:
       - memory_type='schema', source_type='consolidation'
       - raw_text = exemplar text + member count + date range
       - evidence = JSON list of member ids (provenance, recoverable)
       - embedding = medoid vector (consistent with its text)
       - affect = copy of exemplar's affect row
       - 'consolidates' edges schema -> each member
       - members: is_core=0 (demoted from boot working set, NOT
         deleted, status untouched, text untouched)

UNTOUCHABLES (never clustered, never demoted)
---------------------------------------------
  - teachings (source_type='teaching'): user-authored, authoritative
  - counterweights (counterweight_type IS NOT NULL): safety anchors
  - consent_level='high': sensitive, out of scope for v1
  - memories already consolidated into a schema node

SAFETY MODEL
------------
Dry-run by default: writes a JSON report of every proposed cluster to
~/.cama/ and touches nothing. --apply executes exactly what the report
describes. Every apply is reversible from the report (member ids +
prior is_core values are recorded).

USAGE
-----
  python -m cama.sleep.cama_consolidate                  # dry-run, core scope
  python -m cama.sleep.cama_consolidate --scope durable  # whole durable store
  python -m cama.sleep.cama_consolidate --threshold 0.95 --min-size 4
  python -m cama.sleep.cama_consolidate --apply          # execute last analysis
"""

import argparse
import json
import os
import sqlite3
import sys
from typing import Dict, List

import numpy as np

from cama.core import embedding_store as _emb_store
from cama.core.time_utils import now_iso as _now

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
REPORT_DIR = os.environ.get("CAMA_REPORT_DIR", os.path.dirname(DB_PATH))

DEFAULT_THRESHOLD = 0.92
DEFAULT_MIN_SIZE = 3
CHUNK = 1024  # rows per cosine block; 1024 x 50k x 4B ≈ 200MB peak


def get_db() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    _emb_store.ensure_blob_column(c)
    return c


# ============================================================
# Candidate selection
# ============================================================
def load_candidates(c, scope: str):
    """Rows eligible for consolidation. Untouchables filtered here."""
    q = """
        SELECT m.id, m.raw_text, m.memory_type, m.source_type, m.created_at,
               m.is_core, m.access_count
        FROM memories m
        WHERE m.status = 'durable'
          AND m.source_type != 'teaching'
          AND m.counterweight_type IS NULL
          AND m.consent_level != 'high'
          AND m.memory_type != 'schema'
          AND m.id NOT IN (
              SELECT to_id FROM edges WHERE edge_type = 'consolidates'
          )
    """
    if scope == "core":
        q += " AND m.is_core = 1"
    return c.execute(q).fetchall()


# ============================================================
# Clustering: chunked cosine + union-find
# ============================================================
class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_near_duplicates(mat: np.ndarray, threshold: float) -> Dict[int, List[int]]:
    """Union-find over all pairs with cosine >= threshold.

    Note: union-find chains (A~B, B~C merges A,C even if A-C < threshold).
    Acceptable at duplicate-level thresholds; the medoid exemplar guards
    against drift in what the schema node claims to represent.
    """
    n = mat.shape[0]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = mat / norms
    uf = _UnionFind(n)
    for start in range(0, n, CHUNK):
        block = unit[start : start + CHUNK]
        sims = block @ unit.T  # (chunk, n)
        rows, cols = np.nonzero(sims >= threshold)
        for i, j in zip(rows, cols):
            gi = start + int(i)
            gj = int(j)
            if gj > gi:  # upper triangle only, skip self
                uf.union(gi, gj)
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return groups


def medoid_index(mat: np.ndarray, members: List[int]) -> int:
    sub = mat[members]
    norms = np.linalg.norm(sub, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = sub / norms
    sims = unit @ unit.T
    return members[int(np.argmax(sims.mean(axis=1)))]


# ============================================================
# Analysis (dry-run)
# ============================================================
def analyze(scope: str, threshold: float, min_size: int) -> dict:
    c = get_db()
    try:
        rows = load_candidates(c, scope)
        if not rows:
            return {"clusters": [], "note": "no eligible candidates"}
        mids = [r["id"] for r in rows]
        by_id = {r["id"]: r for r in rows}
        emb_map = _emb_store.fetch_emb_map(c, mids)
        usable = [m for m in mids if m in emb_map]
        print(f"scope={scope}: {len(rows)} eligible, {len(usable)} with embeddings")

        mat = np.stack([emb_map[m] for m in usable]).astype(np.float32)
        groups = cluster_near_duplicates(mat, threshold)

        clusters = []
        for members_idx in groups.values():
            if len(members_idx) < min_size:
                continue
            ex_idx = medoid_index(mat, members_idx)
            member_ids = [usable[i] for i in members_idx]
            ex_id = usable[ex_idx]
            dates = sorted(
                d for d in (by_id[m]["created_at"] for m in member_ids) if d
            )
            clusters.append({
                "exemplar_id": ex_id,
                "exemplar_text": by_id[ex_id]["raw_text"][:300],
                "member_ids": member_ids,
                "prior_is_core": {str(m): by_id[m]["is_core"] for m in member_ids},
                "size": len(member_ids),
                "date_range": [dates[0][:10], dates[-1][:10]] if dates else None,
                "sample_texts": [by_id[m]["raw_text"][:120] for m in member_ids[:5]],
            })
        clusters.sort(key=lambda x: x["size"], reverse=True)

        total_members = sum(cl["size"] for cl in clusters)
        core_now = c.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE is_core=1"
        ).fetchone()["n"]
        report = {
            "generated_at": _now(),
            "db": DB_PATH,
            "scope": scope,
            "threshold": threshold,
            "min_size": min_size,
            "eligible": len(rows),
            "clusters_found": len(clusters),
            "memories_in_clusters": total_members,
            "schema_nodes_proposed": len(clusters),
            "core_size_now": core_now,
            "core_size_after_apply": core_now - sum(
                sum(cl["prior_is_core"].values()) for cl in clusters
            ) + len(clusters),
            "clusters": clusters,
        }
        return report
    finally:
        c.close()


# ============================================================
# Apply
# ============================================================
def apply_report(report: dict) -> dict:
    c = get_db()
    created, demoted = 0, 0
    try:
        for cl in report["clusters"]:
            ts = _now()
            ex = c.execute(
                "SELECT raw_text FROM memories WHERE id=?", (cl["exemplar_id"],)
            ).fetchone()
            if ex is None:
                continue
            dr = cl.get("date_range")
            span = f"{dr[0]} to {dr[1]}" if dr else "unknown range"
            text = (
                f"[schema: {cl['size']} consolidated memories, {span}] "
                f"{ex['raw_text']}"
            )
            cur = c.execute(
                """INSERT INTO memories
                   (raw_text, summary, memory_type, source_type, status,
                    proposed_by, evidence, confidence, consent_level,
                    created_at, updated_at, is_core)
                   VALUES (?, NULL, 'schema', 'consolidation', 'durable',
                           'system', ?, 1.0, 'low', ?, ?, 1)""",
                (text, json.dumps(cl["member_ids"]), ts, ts),
            )
            schema_id = cur.lastrowid

            # Schema node inherits the medoid's embedding + affect
            emb = c.execute(
                "SELECT embedding_blob, model FROM memory_embeddings WHERE memory_id=?",
                (cl["exemplar_id"],),
            ).fetchone()
            if emb and emb["embedding_blob"]:
                c.execute(
                    "INSERT OR REPLACE INTO memory_embeddings "
                    "(memory_id, embedding_blob, embedding_json, model, computed_at) "
                    "VALUES (?,?,NULL,?,?)",
                    (schema_id, emb["embedding_blob"], emb["model"], ts),
                )
            af = c.execute(
                "SELECT valence, arousal, dominance, emotion_json, confidence, model "
                "FROM memory_affect WHERE memory_id=?",
                (cl["exemplar_id"],),
            ).fetchone()
            if af:
                c.execute(
                    "INSERT OR IGNORE INTO memory_affect "
                    "(memory_id, valence, arousal, dominance, emotion_json, "
                    " confidence, computed_at, model) VALUES (?,?,?,?,?,?,?,?)",
                    (schema_id, af["valence"], af["arousal"], af["dominance"],
                     af["emotion_json"], af["confidence"], ts, af["model"]),
                )

            for mid in cl["member_ids"]:
                c.execute(
                    """INSERT OR IGNORE INTO edges
                       (from_id, to_id, edge_type, weight, rationale, created_at)
                       VALUES (?, ?, 'consolidates', 0.9, ?, ?)""",
                    (schema_id, mid, f"consolidation {report['generated_at']}", ts),
                )
                c.execute("UPDATE memories SET is_core=0, updated_at=? WHERE id=?", (ts, mid))
                demoted += 1
            created += 1
            c.commit()
        core_after = c.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE is_core=1"
        ).fetchone()["n"]
        return {"schema_nodes_created": created, "members_demoted": demoted,
                "core_size_now": core_after}
    finally:
        c.close()


# ============================================================
# CLI
# ============================================================
def _report_path() -> str:
    return os.path.join(REPORT_DIR, "consolidation_report_latest.json")


def main():
    ap = argparse.ArgumentParser(description="CAMA schematization pass")
    ap.add_argument("--scope", choices=["core", "durable"], default="core")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    ap.add_argument("--apply", action="store_true",
                    help="execute the most recent analysis report")
    args = ap.parse_args()

    if args.apply:
        path = _report_path()
        if not os.path.exists(path):
            print(f"No analysis report at {path}. Run without --apply first.")
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        print(f"Applying report from {report['generated_at']}: "
              f"{report['clusters_found']} clusters, "
              f"{report['memories_in_clusters']} memories")
        result = apply_report(report)
        # Archive the applied report so it can't be double-applied
        os.replace(path, path.replace("latest", f"applied_{report['generated_at'][:19].replace(':', '')}"))
        print(json.dumps(result, indent=2))
        return

    report = analyze(args.scope, args.threshold, args.min_size)
    path = _report_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in report.items() if k != "clusters"}, indent=2))
    print(f"\nFull report: {path}")
    if report.get("clusters"):
        print("Top clusters:")
        for cl in report["clusters"][:8]:
            print(f"  [{cl['size']:4d}] {cl['exemplar_text'][:90]}")
        print("\nDry-run only. Review the report, then re-run with --apply.")


if __name__ == "__main__":
    main()
