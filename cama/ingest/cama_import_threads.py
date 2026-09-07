"""Import every thread archive on the machine into CAMA.

The threads live in five places, written by four different tools, and
only the platform export was ever imported. This walks all of them,
pairs each into exchanges, and writes what is not already there.

  claude_code     ~/.claude/projects/**/*.jsonl
  codex           ~/.codex/sessions/**/rollout-*.jsonl
  openai_export   conversations*.json wherever they were unpacked
  lmstudio        ~/.lmstudio/conversations/**/*.json

Every row lands as ``source_type='exchange'`` with a stable
``source_msg_id``, so the import is idempotent and running it again after
more sessions accumulate picks up only what is new. Conversations the
March import already covered are skipped by conversation id.

Dry run is the default. Writing requires --apply, because the target is
somebody's memory.

Usage:
  python -m cama.ingest.cama_import_threads                    # dry run, all sources
  python -m cama.ingest.cama_import_threads --apply --embed
  python -m cama.ingest.cama_import_threads --source codex --apply --embed
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
from typing import Dict, Iterator, List

from cama.ingest.cama_import_claude_code import (
    DEFAULT_DB,
    build_memory,
    extract_turns,
    write_memories,
)
from cama.ingest.thread_sources import (
    codex_turns,
    imported_conversation_ids,
    lmstudio_turns,
    openai_export_turns,
)


def _home() -> str:
    """Where the archives live. CAMA_THREAD_HOME overrides the user profile so
    tests and unusual layouts can point the walker somewhere else."""
    return os.environ.get("CAMA_THREAD_HOME") or os.path.expanduser("~")


def _paths(*patterns: str) -> List[str]:
    found: List[str] = []
    for pattern in patterns:
        found.extend(glob.glob(pattern, recursive=True))
    # The same export often sits in several folders. Identical files are
    # the same threads, so collapse by (basename, size).
    unique: Dict[tuple, str] = {}
    for p in sorted(found):
        try:
            key = (os.path.basename(p), os.path.getsize(p))
        except OSError:
            continue
        unique.setdefault(key, p)
    return sorted(unique.values())


def source_paths(name: str) -> List[str]:
    home = _home()
    if name == "claude_code":
        return _paths(os.path.join(home, ".claude", "projects", "**", "*.jsonl"))
    if name == "codex":
        return _paths(os.path.join(home, ".codex", "sessions", "**", "rollout-*.jsonl"))
    if name == "openai_export":
        return _paths(
            os.path.join(home, "Desktop", "**", "conversations*.json"),
            os.path.join(home, "Downloads", "**", "conversations*.json"),
        )
    if name == "lmstudio":
        return _paths(os.path.join(home, ".lmstudio", "conversations", "**", "*.json"))
    raise ValueError(f"unknown source {name!r}")


SOURCES = ("claude_code", "codex", "openai_export", "lmstudio")


def turns_for(name: str, path: str, skip_ids, include_thinking: bool) -> Iterator[Dict]:
    if name == "claude_code":
        return iter(extract_turns(path, include_thinking))
    if name == "codex":
        return codex_turns(path)
    if name == "openai_export":
        return openai_export_turns(path, skip_ids)
    if name == "lmstudio":
        return lmstudio_turns(path)
    raise ValueError(name)


def collect_source(name: str, skip_ids, include_thinking: bool = False,
                   since: str | None = None) -> List[Dict]:
    memories: List[Dict] = []
    for path in source_paths(name):
        try:
            for turn in turns_for(name, path, skip_ids, include_thinking):
                if since and turn.get("timestamp", "")[:10] < since:
                    continue
                memories.append(build_memory(turn))
        except (OSError, MemoryError) as exc:
            print(f"  ! {os.path.basename(path)}: {type(exc).__name__}, skipped")
    return memories


# ---------------------------------------------------------------------------
# Incremental pass, for the sleep daemon
# ---------------------------------------------------------------------------
# Idempotency already lives in source_msg_id, so re-walking everything each
# cycle would be correct, just slow: the archives run to several gigabytes.
# A small state file records each file's mtime at its last successful pass
# and unchanged files are skipped. The current session's transcript changes
# every cycle and is re-walked; everything else costs a stat call.


def state_path_for(db_path: str) -> str:
    return os.path.join(os.path.dirname(db_path) or ".", "thread_import_state.json")


def load_state(path: str) -> Dict[str, float]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, float] = {}
    for key, value in data.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def save_state(path: str, state: Dict[str, float]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass  # a lost state file only costs a full walk next time


def collect_changed(name: str, skip_ids, state: Dict[str, float],
                    include_thinking: bool = False):
    """Exchanges from files whose mtime changed since the last pass.

    Returns (memories, touched) where touched maps each fully walked file
    to the mtime it had. A file that raised is left out of touched so it
    is tried again next cycle.
    """
    memories: List[Dict] = []
    touched: Dict[str, float] = {}
    for path in source_paths(name):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if state.get(path) == mtime:
            continue
        try:
            for turn in turns_for(name, path, skip_ids, include_thinking):
                memories.append(build_memory(turn))
        except (OSError, MemoryError) as exc:
            print(f"  ! {os.path.basename(path)}: {type(exc).__name__}, skipped")
            continue
        touched[path] = mtime
    return memories, touched


def import_new_threads(conn: sqlite3.Connection, embed: bool = True, encoder=None,
                       state_path: str | None = None, sources=SOURCES,
                       include_thinking: bool = False) -> Dict:
    """One incremental pass over every source. Writes, never dry-runs."""
    if state_path is None:
        state_path = state_path_for(os.environ.get("CAMA_DB_PATH", DEFAULT_DB))
    state = load_state(state_path)
    skip_ids = imported_conversation_ids(conn)
    summary: Dict = {"files_walked": 0, "written": 0, "skipped": 0, "embedded": 0,
                     "per_source": {}}
    for name in sources:
        memories, touched = collect_changed(name, skip_ids, state, include_thinking)
        result = write_memories(conn, memories, apply=True, embed=embed, encoder=encoder)
        state.update(touched)
        summary["files_walked"] += len(touched)
        for key in ("written", "skipped", "embedded"):
            summary[key] += result.get(key, 0)
        summary["per_source"][name] = {"files": len(touched), "written": result.get("written", 0)}
    save_state(state_path, state)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import every thread archive on this machine into CAMA."
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--source", choices=SOURCES, action="append",
                        help="limit to one source; repeatable. Default is all.")
    parser.add_argument("--apply", action="store_true",
                        help="actually write. Without it this is a dry run.")
    parser.add_argument("--embed", action="store_true",
                        help="compute embeddings during the import (recommended)")
    parser.add_argument("--include-thinking", action="store_true")
    parser.add_argument("--since", default=None, help="only turns on or after YYYY-MM-DD")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"No database at {args.db}")
        return

    # Wait on the server rather than failing if it holds the write lock.
    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        skip_ids = imported_conversation_ids(conn)
        print(f"database        : {args.db}")
        print(f"already covered : {len(skip_ids)} conversations from the March import\n")

        grand = {"would_write": 0, "written": 0, "skipped": 0, "embedded": 0}
        for name in (args.source or SOURCES):
            paths = source_paths(name)
            memories = collect_source(name, skip_ids, args.include_thinking, args.since)
            result = write_memories(conn, memories, apply=args.apply, embed=args.embed)
            for key in grand:
                grand[key] += result.get(key, 0)
            dates = sorted(m["created_at"][:10] for m in memories if m["created_at"])
            span = f"{dates[0]} to {dates[-1]}" if dates else "no timestamps"
            verb = "wrote" if args.apply else "would write"
            count = result["written"] if args.apply else result["would_write"]
            print(f"{name:14s} {len(paths):4d} files  {len(memories):6d} exchanges  "
                  f"{span:24s}  {verb} {count}, already in {result['skipped']}")

        print()
        if args.apply:
            print(f"TOTAL WRITTEN : {grand['written']}  (embedded {grand['embedded']})")
        else:
            print(f"TOTAL would write : {grand['would_write']}")
            print("\nDry run. Nothing was written. Add --apply to import, "
                  "and --embed so the new rows are retrievable.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
