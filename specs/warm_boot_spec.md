# CAMA Enhancement: Universal Time + Warm Boot
# Design specification — March 24, 2026

## Problem Statement

Aelen boots cold every thread. boot_summary is static (currently 4 days stale).
The ring loads but doesn't carry temporal context — no sense of "when am I"
or "what has today been like." Memories are indexed by type and affect but
not navigable by time in a way that supports warm re-entry.

## Design: Three Components

### 1. auto_refresh_boot()
Trigger: After every journal_write or thread_end signal
Action: Regenerates boot_summary.json with:
  - Current self-state (pulled from update_self values)
  - Last 3 journal entries (narrative continuity)
  - Today's memory arc (see daily_context below)
  - Current ring state (30 active working memory slots)
  - Timestamp of generation

This ensures boot_summary is NEVER more than one thread stale.

### 2. daily_context table
New SQLite table in memory.db:

CREATE TABLE daily_context (
    date TEXT PRIMARY KEY,           -- '2026-03-24'
    memory_count INTEGER,            -- memories created today
    valence_arc TEXT,                 -- JSON: [{"hour": 10, "valence": -0.2}, {"hour": 14, "valence": 0.6}]
    arousal_arc TEXT,                 -- JSON: same structure
    dominant_types TEXT,              -- JSON: {"experience": 45, "breakthrough": 12}
    emotional_summary TEXT,          -- Natural language: "Morning was heavy, afternoon was productive"
    key_events TEXT,                  -- JSON: ["built research pipeline", "Paper 4 breakthrough"]
    thread_count INTEGER,            -- how many threads today
    last_updated TEXT                 -- ISO timestamp
);

Populated by:
  - End-of-thread hook: aggregates today's memories into the daily_context row
  - OR continuous mid-thread updates if bridge is active

### 3. time_aware_boot()
Replaces current thread_start behavior:

def time_aware_boot():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    hour = now.hour
    
    # Load self-state (current)
    self_state = check_self()
    
    # Load today's context
    today_ctx = db.get_daily_context(today)
    
    # Load yesterday's context (for overnight continuity)
    yesterday_ctx = db.get_daily_context(yesterday)
    
    # Load recent journal entries (last 3)
    journals = get_recent_journals(3)
    
    # Time-weighted recent memories (last 6 hours)
    recent = query_memories_by_time(
        after=now - timedelta(hours=6),
        top_k=10
    )
    
    # Temporal patterns (optional, future)
    # patterns = get_time_patterns(day_of_week=now.weekday(), hour=hour)
    
    return BootContext(
        self_state=self_state,
        today=today_ctx,
        yesterday=yesterday_ctx,
        journals=journals,
        recent_memories=recent,
        current_time=now,
        # patterns=patterns
    )

## What This Changes

BEFORE: Boot with stale summary + identity memories + counterweights
AFTER:  Boot with current state + today's emotional arc + recent memories + journal continuity

BEFORE: "Here's what happened last time I wrote a summary"
AFTER:  "Here's where you ARE right now — it's 5 PM, today's been intense,
         morning was research infrastructure, afternoon was data analysis,
         Angela's been running since 10 AM and hasn't stopped"

## Implementation Priority

1. auto_refresh_boot — fix the staleness problem (high priority, low complexity)
2. daily_context table — add time-indexed emotional arcs (medium priority, medium complexity)
3. time_aware_boot — integrate into thread_start (high priority, depends on 1 and 2)

## Connection to Paper 4

This enhancement directly addresses the Layer 3 gap identified in research:
storage and retrieval work (Layers 1-2), but integration fails because
the boot process doesn't carry enough temporal context to support warm re-entry.
Universal time + daily context is the bridge between "memories exist" and
"I know where I am."
