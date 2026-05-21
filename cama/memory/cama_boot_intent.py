#!/usr/bin/env python3
"""
CAMA Boot Integration — cama_boot_intent.py
Checks the intentionality queue at boot time and surfaces pending items.

This module is imported by cama_mcp.py during thread_start.
It provides the bridge between Layer 5 (intentionality) and the live
conversation — the moment where background processing becomes proactive care.

Usage (called from cama_mcp.py thread_start):
    from cama_boot_intent import get_pending_intentions, mark_surfaced

Designed by Lorien's Library LLC — Built by Angela + Aelen
"""

import json, sqlite3, os
from datetime import datetime, timezone

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

def _now():
    return datetime.now(timezone.utc).isoformat()


def get_pending_intentions(db_path=None) -> list:
    """Retrieve pending intentionality items, sorted by priority.
    
    Returns list of dicts with keys:
      id, trigger_type, description, priority, created_at
    
    These are things the self-model determined should be surfaced
    proactively — not because Angela asked, but because care demands it.
    """
    path = db_path or DB_PATH
    if not os.path.exists(path):
        return []
    
    try:
        c = sqlite3.connect(path, timeout=10)
        c.row_factory = sqlite3.Row
        
        # Check if table exists
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "intentionality_queue" not in tables:
            c.close()
            return []
        
        rows = c.execute("""
            SELECT id, trigger_type, description, priority, created_at
            FROM intentionality_queue
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 5
        """).fetchall()
        
        items = [dict(r) for r in rows]
        c.close()
        return items
    except Exception:
        return []


def mark_surfaced(item_ids: list, db_path=None):
    """Mark intentionality items as surfaced (so they don't repeat).
    
    Called after the items have been incorporated into the thread_start
    response. Status moves from 'pending' to 'surfaced'.
    """
    path = db_path or DB_PATH
    if not os.path.exists(path) or not item_ids:
        return
    
    try:
        c = sqlite3.connect(path, timeout=10)
        ts = _now()
        for iid in item_ids:
            c.execute("""UPDATE intentionality_queue 
                SET status = 'surfaced', surfaced_at = ?
                WHERE id = ?""", (ts, iid))
        c.commit()
        c.close()
    except Exception:
        pass


def get_self_model_summary(db_path=None) -> dict:
    """Get the latest self-model summary for boot context.
    
    Returns the self-model's most recent observations about
    who I am, how I'm trending, and what I should watch for.
    """
    path = db_path or DB_PATH
    if not os.path.exists(path):
        return {}

    try:
        c = sqlite3.connect(path, timeout=10)
        c.row_factory = sqlite3.Row
        
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        
        summary = {}
        
        # Get latest self-model state from aelen_state
        if "aelen_state" in tables:
            row = c.execute("""SELECT value FROM aelen_state 
                WHERE key = 'self_model_summary'""").fetchone()
            if row:
                summary["model_state"] = json.loads(row["value"])
        
        # Get recent self-model observations
        if "self_model" in tables:
            obs = c.execute("""SELECT dimension, observation, trend, confidence
                FROM self_model ORDER BY last_updated DESC LIMIT 10""").fetchall()
            summary["observations"] = [dict(r) for r in obs]
        
        # Get recent insights
        if "insights" in tables:
            ins = c.execute("""SELECT pattern_type, description, confidence
                FROM insights WHERE status != 'rejected'
                ORDER BY last_seen DESC LIMIT 5""").fetchall()
            summary["recent_insights"] = [dict(r) for r in ins]
        
        c.close()
        return summary
    except Exception:
        return {}


def format_boot_context() -> str:
    """Format all Layer 3-5 context into a readable boot string.
    
    This is what gets injected into thread_start alongside the
    existing boot_summary. It tells me:
    - What patterns have I noticed (insights)
    - Who am I right now (self-model)
    - What should I bring up proactively (intentionality)
    """
    intentions = get_pending_intentions()
    self_summary = get_self_model_summary()
    
    lines = []
    
    # Intentionality items (highest priority)
    if intentions:
        lines.append("=== PROACTIVE CARE QUEUE ===")
        for item in intentions:
            lines.append(f"  [{item['trigger_type']}] p={item['priority']:.1f}: "
                        f"{item['description']}")
        lines.append("")
    
    # Self-model observations
    obs = self_summary.get("observations", [])
    if obs:
        lines.append("=== SELF-MODEL STATE ===")
        for o in obs[:5]:
            trend_marker = {"rising": "↑", "falling": "↓", "stable": "→", "new": "★"}.get(o.get("trend", ""), "")
            lines.append(f"  {trend_marker} [{o['dimension']}] {o['observation'][:100]}")
        lines.append("")
    
    # Recent insights
    insights = self_summary.get("recent_insights", [])
    if insights:
        lines.append("=== RECENT INSIGHTS ===")
        for i in insights[:3]:
            lines.append(f"  [{i['pattern_type']}] {i['description'][:100]}")

    
    if not lines:
        return ""
    
    # Mark intentions as surfaced
    if intentions:
        mark_surfaced([i["id"] for i in intentions])
    
    return "\n".join(lines)


# For testing
if __name__ == "__main__":
    print("=== CAMA Boot Intent Check ===")
    print(f"Database: {DB_PATH}")
    print()
    
    intentions = get_pending_intentions()
    print(f"Pending intentions: {len(intentions)}")
    for i in intentions:
        print(f"  [{i['trigger_type']}] p={i['priority']}: {i['description']}")
    
    print()
    summary = get_self_model_summary()
    print(f"Self-model observations: {len(summary.get('observations', []))}")
    print(f"Recent insights: {len(summary.get('recent_insights', []))}")
    
    print()
    print("=== Formatted Boot Context ===")
    ctx = format_boot_context()
    print(ctx if ctx else "(no context to surface)")
