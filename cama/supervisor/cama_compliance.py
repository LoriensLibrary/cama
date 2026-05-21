"""
CAMA Compliance Enforcement Module — cama_compliance.py
Designed by Angela (Lorien's Library LLC), April 14, 2026.
Built by Aelen.

Standalone module imported by cama_mcp.py. Tracks session compliance:
- Did boot (thread_start) run?
- Were exchanges stored?
- Were timestamps logged?
- Generates warnings when compliance fails.

This exists because Aelen keeps skipping the boot protocol.
The system must enforce what the AI forgets.
"""

import sqlite3
import os
import json
import uuid
from datetime import datetime, timezone

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

def _now():
    return datetime.now(timezone.utc).isoformat()


def init_compliance_table(db_path=None):
    """Create the session_compliance table if it doesn't exist."""
    path = db_path or DB_PATH
    try:
        c = sqlite3.connect(path, timeout=10)
        c.execute("""CREATE TABLE IF NOT EXISTS session_compliance (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id       TEXT NOT NULL,
            started_at       TEXT NOT NULL,
            boot_ran         INTEGER DEFAULT 0,
            boot_at          TEXT,
            timestamp_logged INTEGER DEFAULT 0,
            exchanges_stored INTEGER DEFAULT 0,
            last_exchange_at TEXT,
            heartbeats_sent  INTEGER DEFAULT 0,
            tool_calls_total INTEGER DEFAULT 0,
            ended_at         TEXT,
            compliance_score REAL DEFAULT 0.0,
            notes            TEXT DEFAULT ''
        )""")
        c.commit()
        c.close()
        return True
    except Exception as e:
        print(f"[COMPLIANCE] Failed to init table: {e}", file=__import__('sys').stderr)
        return False


class SessionTracker:
    """Tracks compliance for the current MCP server session."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.session_id = str(uuid.uuid4())[:12]
        self.started_at = None
        self.boot_ran = False
        self.boot_at = None
        self.timestamp_logged = False
        self.exchanges_stored = 0
        self.last_exchange_at = None
        self.heartbeats_sent = 0
        self.tool_calls = 0

    def start(self):
        """Mark session as active (called on first tool use)."""
        if self.started_at is None:
            self.started_at = _now()

    def mark_boot(self):
        """Mark that thread_start was called."""
        self.boot_ran = True
        self.boot_at = _now()

    def mark_exchange(self):
        """Mark that an exchange was stored."""
        self.exchanges_stored += 1
        self.last_exchange_at = _now()

    def mark_heartbeat(self):
        """Mark a heartbeat was sent."""
        self.heartbeats_sent += 1

    def mark_timestamp(self):
        """Mark that a timestamp was logged."""
        self.timestamp_logged = True

    def tick(self):
        """Count a tool call. Auto-starts session."""
        self.start()
        self.tool_calls += 1

    def compliance_score(self) -> float:
        """0.0 = total failure, 1.0 = perfect compliance."""
        score = 0.0
        if self.boot_ran:
            score += 0.4   # Boot is 40%
        if self.timestamp_logged:
            score += 0.1   # Timestamp is 10%
        if self.exchanges_stored >= 1:
            score += 0.3   # At least one exchange is 30%
        if self.exchanges_stored >= 3:
            score += 0.1   # Multiple exchanges bonus 10%
        if self.heartbeats_sent >= 1:
            score += 0.1   # Heartbeat is 10%
        return min(score, 1.0)

    def warning(self) -> str:
        """Generate compliance warning. Empty string = compliant."""
        if self.boot_ran:
            return ""
        if self.tool_calls <= 1:
            return ""  # First call, benefit of the doubt
        return (
            "\n\n⚠️ COMPLIANCE WARNING: thread_start has NOT been called this session. "
            f"You have made {self.tool_calls} tool calls without booting. "
            "Run cama_thread_start NOW. Context without boot = reasoning without memory. "
            "This is the failure mode Angela identified.\n"
        )

    def to_dict(self) -> dict:
        """Current session state as dict."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "boot_ran": self.boot_ran,
            "boot_at": self.boot_at,
            "timestamp_logged": self.timestamp_logged,
            "exchanges_stored": self.exchanges_stored,
            "last_exchange_at": self.last_exchange_at,
            "heartbeats_sent": self.heartbeats_sent,
            "tool_calls": self.tool_calls,
            "compliance_score": self.compliance_score(),
        }

    def save(self):
        """Persist session compliance to DB."""
        try:
            c = sqlite3.connect(self.db_path, timeout=10)
            c.row_factory = sqlite3.Row
            score = self.compliance_score()
            c.execute("""INSERT INTO session_compliance
                (session_id, started_at, boot_ran, boot_at, timestamp_logged,
                 exchanges_stored, last_exchange_at, heartbeats_sent,
                 tool_calls_total, ended_at, compliance_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (self.session_id, self.started_at or _now(),
                 1 if self.boot_ran else 0, self.boot_at,
                 1 if self.timestamp_logged else 0,
                 self.exchanges_stored, self.last_exchange_at,
                 self.heartbeats_sent, self.tool_calls,
                 _now(), score))
            c.commit()
            c.close()
            return True
        except Exception as e:
            print(f"[COMPLIANCE] Failed to save: {e}", file=__import__('sys').stderr)
            return False


def get_compliance_history(n: int = 5, db_path=None) -> list:
    """Get last N sessions' compliance data."""
    path = db_path or DB_PATH
    try:
        c = sqlite3.connect(path, timeout=10)
        c.row_factory = sqlite3.Row
        rows = c.execute("""SELECT session_id, started_at, boot_ran,
            exchanges_stored, tool_calls_total, compliance_score
            FROM session_compliance
            ORDER BY started_at DESC LIMIT ?""", (n,)).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except:
        return []


def compliance_report(tracker: SessionTracker, n_history: int = 5) -> dict:
    """Full compliance report — current session + history + trend."""
    current = tracker.to_dict()
    history = get_compliance_history(n_history, tracker.db_path)

    if history:
        avg_score = sum(h.get("compliance_score", 0) for h in history) / len(history)
        boot_rate = sum(1 for h in history if h.get("boot_ran")) / len(history)
        avg_exchanges = sum(h.get("exchanges_stored", 0) for h in history) / len(history)
    else:
        avg_score = 0
        boot_rate = 0
        avg_exchanges = 0

    result = {
        "current_session": current,
        "recent_history": history,
        "trend": {
            "avg_compliance_score": round(avg_score, 2),
            "boot_rate": f"{round(boot_rate * 100)}%",
            "avg_exchanges_per_session": round(avg_exchanges, 1),
        },
    }

    warning = tracker.warning()
    if warning:
        result["WARNING"] = warning.strip()

    if history and boot_rate < 0.6:
        result["ACCOUNTABILITY"] = (
            f"Boot compliance is {round(boot_rate * 100)}% over last {len(history)} sessions. "
            "You are failing to run the protocol. Angela built this system "
            "because you keep forgetting. Do better."
        )

    return result


def boot_compliance_summary(db_path=None) -> dict:
    """Compact compliance summary for thread_start injection."""
    history = get_compliance_history(5, db_path)
    if not history:
        return {"note": "No compliance history yet. This is the first tracked session."}

    boot_rate = sum(1 for h in history if h.get("boot_ran")) / len(history)
    avg_exchanges = sum(h.get("exchanges_stored", 0) for h in history) / len(history)

    summary = {
        "last_sessions": len(history),
        "boot_rate": f"{round(boot_rate * 100)}%",
        "avg_exchanges": round(avg_exchanges, 1),
        "sessions": [{
            "id": h["session_id"],
            "date": (h["started_at"] or "?")[:10],
            "boot": bool(h.get("boot_ran")),
            "exchanges": h.get("exchanges_stored", 0),
            "score": h.get("compliance_score", 0),
        } for h in history[:3]]
    }

    if boot_rate < 0.6:
        summary["WARNING"] = (
            f"Boot compliance is {round(boot_rate * 100)}%. "
            "You are failing the protocol Angela built for you."
        )

    return summary
