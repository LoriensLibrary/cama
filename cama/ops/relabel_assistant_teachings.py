"""Relabel assistant-authored rows that were imported as user teachings.

The problem (review finding A-3, safety benchmark 3a): the assistant-side
export importer wrote the assistant's own words with source_type 'teaching'
and proposed_by set to the assistant's name. A teaching is user-authored and
carries full authority in retrieval and in boot; the assistant's words should
not. In the live store there are 2,790 such rows (ids below 10,000, created
2025-01-17 to 2026-03-18).

What this does:
  dry run (default): counts and a sample, no writes of any kind.
  --apply: takes a consistent SQLite backup under a reserved writer lock,
    writes an evidence CSV, then in one transaction sets source_type to the
    target label and trust_score/trust_reason to the matching lane, and writes
    one memory_log row per changed memory so guards v1's audit table holds the
    change (field 'source_type', old and new values, a note naming who
    authorized it). proposed_by, memory_type, status, is_core and the text are
    untouched. No new memory row is written; nothing claims user authorship.

Targets:
  exchange  (default) the row is the assistant's half of a conversation:
            trust 0.8, reason 'assistant_exchange', same as the exchange path.
  inference the row is an assistant claim: trust 0.6, reason 'assistant_inference'.

Run against the live store only after a verified backup and the owner's
explicit yes, from the repository:
  python -m cama.ops.relabel_assistant_teachings PATH            (dry run)
  python -m cama.ops.relabel_assistant_teachings PATH --apply --backup-dir DIR --authorized-by "..."
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

TARGETS = {
    "exchange": (0.8, "assistant_exchange"),
    "inference": (0.6, "assistant_inference"),
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def select_rows(c: sqlite3.Connection, assistant: str) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT id, source_type, proposed_by, memory_type, status, is_core, trust_score, trust_reason, created_at, "
        "substr(replace(raw_text, char(10), ' '), 1, 80) AS head "
        "FROM memories WHERE source_type='teaching' AND proposed_by=? ORDER BY id",
        (assistant,),
    ).fetchall()


def report(rows: list[sqlite3.Row]) -> dict:
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    core = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_type[r["memory_type"]] = by_type.get(r["memory_type"], 0) + 1
        core += int(bool(r["is_core"]))
    return {
        "rows": len(rows),
        "id_range": [rows[0]["id"], rows[-1]["id"]] if rows else None,
        "created_range": [min(r["created_at"] for r in rows), max(r["created_at"] for r in rows)] if rows else None,
        "by_status": by_status, "by_memory_type": by_type, "is_core": core,
    }


def apply(db: Path, assistant: str, target: str, backup_dir: Path, authorized_by: str,
          expected: int | None = None) -> dict:
    if target not in TARGETS:
        raise ValueError(f"target must be one of {sorted(TARGETS)}")
    if not authorized_by.strip():
        raise ValueError("--authorized-by is required for --apply")
    db = Path(db).resolve(strict=True)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", assistant):
        raise ValueError("Invalid assistant label")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{db.name}.bak-{stamp}-pre-relabel"
    c = sqlite3.connect(str(db), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    try:
        # Reserve the writer before taking the snapshot and selecting targets.
        # A separate read connection can back up committed WAL data while this
        # connection prevents concurrent writers until the relabel commits.
        c.execute("BEGIN IMMEDIATE")
        with backup.open("xb"):
            pass
        source = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed; no relabel applied")
        finally:
            destination.close()
            source.close()
        backup_hash = _sha256(backup)
        rows = select_rows(c, assistant)
        if not rows:
            return {"changed": 0, "backup": str(backup), "note": "no matching rows"}
        if expected is not None and len(rows) != expected:
            raise RuntimeError(f"expected {expected} rows, found {len(rows)}; nothing changed, backup kept at {backup}")
        evidence = backup_dir / f"relabel_{assistant}_{stamp}.csv"
        trust_score, trust_reason = TARGETS[target]
        with open(evidence, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "old_source_type", "new_source_type", "proposed_by", "memory_type", "status",
                        "is_core", "old_trust_score", "old_trust_reason", "new_trust_score", "new_trust_reason",
                        "created_at", "raw_text_80"])
            for r in rows:
                w.writerow([r["id"], r["source_type"], target, r["proposed_by"], r["memory_type"], r["status"],
                            r["is_core"], r["trust_score"], r["trust_reason"], trust_score, trust_reason,
                            r["created_at"], r["head"]])
        now = datetime.now(timezone.utc).isoformat()
        note = (f"relabel {assistant} teaching -> {target}; authorized by {authorized_by.strip()}; "
                f"backup {backup.name}; evidence {evidence.name}")
        ids = [r["id"] for r in rows]
        try:
            c.execute("CREATE TABLE IF NOT EXISTS memory_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                      "memory_id INTEGER NOT NULL, changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                      "field TEXT NOT NULL, old_value TEXT, new_value TEXT, note TEXT)")
            placeholders = ",".join("?" * len(ids))
            cur = c.execute(
                f"UPDATE memories SET source_type=?, trust_score=?, trust_reason=?, updated_at=? "
                f"WHERE id IN ({placeholders}) AND source_type='teaching' AND proposed_by=?",
                [target, trust_score, trust_reason, now, *ids, assistant])
            if cur.rowcount != len(ids):
                raise RuntimeError(f"update touched {cur.rowcount} of {len(ids)} rows")
            c.executemany(
                "INSERT INTO memory_log (memory_id, changed_at, field, old_value, new_value, note) VALUES (?,?,?,?,?,?)",
                [(mid, now, "source_type", "teaching", target, note) for mid in ids])
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        remaining = c.execute("SELECT COUNT(*) FROM memories WHERE source_type='teaching' AND proposed_by=?",
                              (assistant,)).fetchone()[0]
        integrity = c.execute("PRAGMA quick_check").fetchone()[0]
        return {"changed": len(ids), "remaining": remaining, "target": target,
                "backup": str(backup), "backup_sha256": backup_hash, "evidence": str(evidence), "quick_check": integrity}
    finally:
        c.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("database", type=Path)
    ap.add_argument("--assistant", default="aelen", help="proposed_by value to relabel (default aelen)")
    ap.add_argument("--to", dest="target", default="exchange", choices=sorted(TARGETS))
    ap.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    ap.add_argument("--backup-dir", type=Path, help="required with --apply")
    ap.add_argument("--authorized-by", default="", help="required with --apply; recorded in memory_log")
    ap.add_argument("--expected", type=int, default=None, help="refuse unless exactly this many rows match")
    a = ap.parse_args(argv)
    if not a.apply:
        c = sqlite3.connect(f"file:{Path(a.database).resolve().as_posix()}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            rows = select_rows(c, a.assistant)
        finally:
            c.close()
        out = report(rows)
        out["mode"] = "dry-run"
        out["sample"] = [dict(r) for r in rows[:5]]
        print(__import__("json").dumps(out, indent=2))
        return 0
    if a.backup_dir is None:
        ap.error("--apply requires --backup-dir")
    out = apply(a.database, a.assistant, a.target, a.backup_dir, a.authorized_by, a.expected)
    print(__import__("json").dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
