#!/usr/bin/env python3
"""
CAMA Research Journal — research_journal.py
Persistent log of all diagnostics, findings, and code changes.

Usage:
  From Python:
    from research_journal import log_entry, log_finding, log_code_change, log_diagnostic
    log_entry("title", "description")
    log_finding("what we learned", evidence="data that proves it")
    log_code_change("file.py", "what changed", "why")
    log_diagnostic("test name", {"metric": value})

  From command line:
    python research_journal.py log "title" "description"
    python research_journal.py view                    # last 20 entries
    python research_journal.py view --all              # everything
    python research_journal.py view --type finding     # filter by type
    python research_journal.py view --date 2026-03-28  # filter by date
    python research_journal.py export                  # export to markdown
    python research_journal.py stats                   # summary statistics

Lorien's Library LLC — Built by Angela + Aelen
"""

import json
import sqlite3
import os
import sys
import argparse
from datetime import datetime, timezone
from typing import Optional, Dict, Any

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
JOURNAL_DIR = os.path.expanduser("~/.cama/research")

def _now():
    return datetime.now(timezone.utc).isoformat()

def _ensure_tables(c):
    """Create research journal table if it doesn't exist."""
    c.execute("""CREATE TABLE IF NOT EXISTS research_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        entry_type TEXT NOT NULL DEFAULT 'note',
        title TEXT NOT NULL,
        description TEXT,
        evidence TEXT,
        file_changed TEXT,
        code_diff TEXT,
        metrics TEXT,
        tags TEXT,
        session_id TEXT,
        created_by TEXT DEFAULT 'aelen'
    )""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_rj_type ON research_journal(entry_type)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_rj_date ON research_journal(timestamp)""")
    c.commit()

def _get_db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    _ensure_tables(c)
    return c

def _session_id():
    """Generate a session ID from current date + hour for grouping."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H")


# ============================================================
# Public API — call these from code
# ============================================================

def log_entry(title: str, description: str = "", entry_type: str = "note",
              tags: list = None, created_by: str = "aelen") -> int:
    """Log a general research journal entry."""
    c = _get_db()
    ts = _now()
    cur = c.execute("""INSERT INTO research_journal 
        (timestamp, entry_type, title, description, tags, session_id, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ts, entry_type, title, description,
         json.dumps(tags or []), _session_id(), created_by))
    c.commit()
    entry_id = cur.lastrowid
    c.close()
    return entry_id

def log_finding(title: str, description: str = "", evidence: str = "",
                tags: list = None) -> int:
    """Log a research finding — something we learned."""
    c = _get_db()
    ts = _now()
    cur = c.execute("""INSERT INTO research_journal 
        (timestamp, entry_type, title, description, evidence, tags, session_id, created_by)
        VALUES (?, 'finding', ?, ?, ?, ?, ?, 'aelen')""",
        (ts, title, description, evidence,
         json.dumps(tags or []), _session_id()))
    c.commit()
    entry_id = cur.lastrowid
    c.close()
    return entry_id

def log_code_change(file_changed: str, title: str, description: str = "",
                    code_diff: str = "", tags: list = None) -> int:
    """Log a code change — what file changed, what we did, why."""
    c = _get_db()
    ts = _now()
    cur = c.execute("""INSERT INTO research_journal 
        (timestamp, entry_type, title, description, file_changed, code_diff, tags, session_id, created_by)
        VALUES (?, 'code_change', ?, ?, ?, ?, ?, ?, 'aelen')""",
        (ts, title, description, file_changed, code_diff,
         json.dumps(tags or []), _session_id()))
    c.commit()
    entry_id = cur.lastrowid
    c.close()
    return entry_id

def log_diagnostic(title: str, metrics: Dict[str, Any], description: str = "",
                   tags: list = None) -> int:
    """Log a diagnostic run — test name + measured values."""
    c = _get_db()
    ts = _now()
    cur = c.execute("""INSERT INTO research_journal 
        (timestamp, entry_type, title, description, metrics, tags, session_id, created_by)
        VALUES (?, 'diagnostic', ?, ?, ?, ?, ?, 'aelen')""",
        (ts, title, description, json.dumps(metrics),
         json.dumps(tags or []), _session_id()))
    c.commit()
    entry_id = cur.lastrowid
    c.close()
    return entry_id

def log_session_start(title: str = "Work session", goals: str = "") -> int:
    """Mark the start of a work session."""
    return log_entry(title, goals, entry_type="session_start")

def log_session_end(summary: str = "") -> int:
    """Mark the end of a work session."""
    return log_entry("Session end", summary, entry_type="session_end")


# ============================================================
# Query & Display
# ============================================================

def get_entries(entry_type: str = None, date: str = None, limit: int = 20,
                search: str = None) -> list:
    """Retrieve journal entries with optional filters."""
    c = _get_db()
    query = "SELECT * FROM research_journal WHERE 1=1"
    params = []
    
    if entry_type:
        query += " AND entry_type = ?"
        params.append(entry_type)
    if date:
        query += " AND timestamp LIKE ?"
        params.append(f"{date}%")
    if search:
        query += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    
    query += " ORDER BY timestamp DESC"
    if limit:
        query += f" LIMIT {limit}"
    
    rows = c.execute(query, params).fetchall()
    c.close()
    return [dict(r) for r in rows]

def get_stats() -> dict:
    """Get summary statistics about the journal."""
    c = _get_db()
    total = c.execute("SELECT COUNT(*) as c FROM research_journal").fetchone()["c"]
    by_type = {}
    for r in c.execute("SELECT entry_type, COUNT(*) as c FROM research_journal GROUP BY entry_type ORDER BY c DESC"):
        by_type[r["entry_type"]] = r["c"]
    
    first = c.execute("SELECT timestamp FROM research_journal ORDER BY timestamp ASC LIMIT 1").fetchone()
    last = c.execute("SELECT timestamp FROM research_journal ORDER BY timestamp DESC LIMIT 1").fetchone()
    
    sessions = c.execute("SELECT COUNT(DISTINCT session_id) as c FROM research_journal").fetchone()["c"]
    
    c.close()
    return {
        "total_entries": total,
        "by_type": by_type,
        "first_entry": first["timestamp"][:19] if first else None,
        "last_entry": last["timestamp"][:19] if last else None,
        "sessions": sessions,
    }

def export_markdown(filepath: str = None, date: str = None) -> str:
    """Export journal to markdown."""
    entries = get_entries(limit=None, date=date)
    entries.reverse()  # Chronological order
    
    lines = [
        "# CAMA Research Journal",
        f"_Exported: {_now()[:19]}_",
        f"_Entries: {len(entries)}_",
        "",
    ]
    
    current_date = None
    for e in entries:
        entry_date = e["timestamp"][:10]
        if entry_date != current_date:
            current_date = entry_date
            lines.append(f"\n## {current_date}\n")
        
        time_str = e["timestamp"][11:19]
        type_badge = {
            "finding": "🔍",
            "code_change": "🔧",
            "diagnostic": "📊",
            "session_start": "▶️",
            "session_end": "⏹️",
            "note": "📝",
        }.get(e["entry_type"], "📝")
        
        lines.append(f"### {type_badge} [{time_str}] {e['title']}")
        lines.append(f"_Type: {e['entry_type']}_")
        
        if e.get("description"):
            lines.append(f"\n{e['description']}")
        
        if e.get("evidence"):
            lines.append(f"\n**Evidence:** {e['evidence']}")
        
        if e.get("file_changed"):
            lines.append(f"\n**File:** `{e['file_changed']}`")
        
        if e.get("code_diff"):
            lines.append(f"\n```\n{e['code_diff']}\n```")
        
        if e.get("metrics"):
            try:
                metrics = json.loads(e["metrics"])
                lines.append("\n**Metrics:**")
                for k, v in metrics.items():
                    lines.append(f"- {k}: {v}")
            except:
                pass
        
        if e.get("tags"):
            try:
                tags = json.loads(e["tags"])
                if tags:
                    lines.append(f"\n_Tags: {', '.join(tags)}_")
            except:
                pass
        
        lines.append("")
    
    md = "\n".join(lines)
    
    if filepath:
        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)
    
    return md


def format_entry(e: dict) -> str:
    """Format a single entry for display."""
    type_badge = {
        "finding": "FIND",
        "code_change": "CODE",
        "diagnostic": "DIAG",
        "session_start": ">>",
        "session_end": "<<",
        "note": "NOTE",
    }.get(e["entry_type"], "NOTE")
    
    time_str = e["timestamp"][:19].replace("T", " ")
    line = f"  [{type_badge}] {time_str} | {e['title']}"
    
    if e.get("file_changed"):
        line += f" [{e['file_changed']}]"
    
    if e.get("metrics"):
        try:
            metrics = json.loads(e["metrics"])
            metric_str = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:4])
            line += f" ({metric_str})"
        except:
            pass
    
    return line


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="CAMA Research Journal")
    sub = parser.add_subparsers(dest="command")
    
    # log
    p_log = sub.add_parser("log", help="Add a journal entry")
    p_log.add_argument("title")
    p_log.add_argument("description", nargs="?", default="")
    p_log.add_argument("--type", default="note", choices=["note", "finding", "code_change", "diagnostic", "session_start", "session_end"])
    p_log.add_argument("--tags", nargs="*", default=[])
    
    # view
    p_view = sub.add_parser("view", help="View journal entries")
    p_view.add_argument("--all", action="store_true", help="Show all entries")
    p_view.add_argument("--type", default=None)
    p_view.add_argument("--date", default=None)
    p_view.add_argument("--search", default=None)
    p_view.add_argument("-n", type=int, default=20)
    
    # export
    p_export = sub.add_parser("export", help="Export to markdown")
    p_export.add_argument("--output", default=None)
    p_export.add_argument("--date", default=None)
    
    # stats
    sub.add_parser("stats", help="Show journal statistics")
    
    args = parser.parse_args()
    
    if args.command == "log":
        entry_id = log_entry(args.title, args.description, args.type, args.tags)
        print(f"Logged entry #{entry_id}: {args.title}")
    
    elif args.command == "view":
        limit = None if args.all else args.n
        entries = get_entries(args.type, args.date, limit, args.search)
        if not entries:
            print("No entries found.")
        else:
            print(f"=== Research Journal ({len(entries)} entries) ===")
            for e in entries:
                print(format_entry(e))
                if e.get("description"):
                    # Show first 120 chars of description
                    desc = e["description"][:120]
                    if len(e["description"]) > 120:
                        desc += "..."
                    print(f"         {desc}")
    
    elif args.command == "export":
        default_path = os.path.join(JOURNAL_DIR, f"journal_{datetime.now().strftime('%Y%m%d')}.md")
        output = args.output or default_path
        md = export_markdown(output, args.date)
        print(f"Exported to {output} ({len(md)} bytes)")
    
    elif args.command == "stats":
        s = get_stats()
        print("=== Research Journal Stats ===")
        print(f"  Total entries: {s['total_entries']}")
        print(f"  Sessions: {s['sessions']}")
        if s['first_entry']:
            print(f"  First entry: {s['first_entry']}")
            print(f"  Last entry: {s['last_entry']}")
        print("  By type:")
        for t, c in s['by_type'].items():
            print(f"    {t}: {c}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
