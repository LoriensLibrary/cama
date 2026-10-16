"""Daily aggregate writes shared by installs with either historical schema."""

import json

from cama.core.time_utils import now_iso


def upsert_daily_context(c, date, values):
    """Update supplied aggregates without erasing another writer's fields."""
    definitions = {
        "memory_count": "INTEGER DEFAULT 0", "valence_mean": "REAL", "arousal_mean": "REAL",
        "dominant_types": "TEXT DEFAULT '{}'", "dominant_emotions": "TEXT DEFAULT '{}'",
        "key_events": "TEXT DEFAULT '[]'", "thread_summaries": "TEXT DEFAULT '[]'",
        "thread_count": "INTEGER DEFAULT 0", "emotional_arc": "TEXT DEFAULT '[]'",
        "created_at": "TEXT DEFAULT ''", "updated_at": "TEXT DEFAULT ''", "last_updated": "TEXT DEFAULT ''",
    }
    if set(values) - definitions.keys():
        raise ValueError("Unknown daily context field")
    existing = {r["name"] for r in c.execute("PRAGMA table_info(daily_context)")}
    for name, definition in definitions.items():
        if name not in existing:
            c.execute(f"ALTER TABLE daily_context ADD COLUMN {name} {definition}")
    timestamp = now_iso()
    fields = {"date": date, "created_at": timestamp, **values,
              "updated_at": timestamp, "last_updated": timestamp}
    fields = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields if k not in ("date", "created_at"))
    c.execute(f"INSERT INTO daily_context ({columns}) VALUES ({placeholders}) "
              f"ON CONFLICT(date) DO UPDATE SET {updates}", list(fields.values()))
