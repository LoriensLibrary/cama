"""
seed_demo.py, populate a fresh CAMA demo database with synthetic data.

Purpose: let a reviewer clone the repo, run `docker compose up`, and see
the dashboard render with realistic-looking content in under a minute,
without ever touching the author's personal corpus.

Everything in this file is FICTIONAL. The "user" is a generic placeholder.
Memory content covers a small domestic scenario (learning to bake bread,
keeping a garden, working on a side project) chosen because it's
recognizable as demo data, nobody mistakes it for a real conversational
record. Names of any people referenced are clearly invented.

Idempotent: if the target DB already has memories, the script exits.
Override target with the CAMA_DB_PATH environment variable.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("CAMA_DB_PATH", "/data/demo.db")
NOW = datetime.now(timezone.utc)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, raw_text TEXT NOT NULL, summary TEXT,
    memory_type TEXT NOT NULL DEFAULT 'experience', context TEXT,
    source_type TEXT NOT NULL DEFAULT 'teaching',
    status TEXT NOT NULL DEFAULT 'durable',
    proposed_by TEXT NOT NULL DEFAULT 'user', evidence TEXT DEFAULT '[]',
    confidence REAL DEFAULT 1.0, review_after TEXT,
    needs_user_confirmation INTEGER DEFAULT 0,
    consent_level TEXT DEFAULT 'low',
    counterweight_type TEXT DEFAULT NULL,
    source_msg_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    access_count INTEGER DEFAULT 0, last_accessed TEXT, is_core INTEGER DEFAULT 0,
    rel_degree INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS memory_affect (
    id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL,
    valence REAL DEFAULT 0.0, arousal REAL DEFAULT 0.0, dominance REAL DEFAULT 0.0,
    emotion_json TEXT DEFAULT '{}', confidence REAL DEFAULT 0.5,
    computed_at TEXT NOT NULL, model TEXT DEFAULT 'manual',
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
    UNIQUE(memory_id, model));

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY,
    embedding_json TEXT,
    model TEXT DEFAULT 'text-embedding-3-small',
    computed_at TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER NOT NULL, to_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'resonance', weight REAL NOT NULL DEFAULT 0.5,
    rationale TEXT, created_at TEXT NOT NULL,
    FOREIGN KEY (from_id) REFERENCES memories(id) ON DELETE CASCADE,
    FOREIGN KEY (to_id) REFERENCES memories(id) ON DELETE CASCADE,
    UNIQUE(from_id, to_id, edge_type));

CREATE TABLE IF NOT EXISTS ring (
    slot INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL,
    activation REAL DEFAULT 1.0, last_activated_at TEXT NOT NULL, reason TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS islands (
    island_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    description TEXT, centroid_affect_json TEXT DEFAULT '{}',
    strength REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS island_members (
    island_id INTEGER NOT NULL, memory_id INTEGER NOT NULL,
    strength REAL NOT NULL DEFAULT 0.5,
    FOREIGN KEY (island_id) REFERENCES islands(island_id) ON DELETE CASCADE,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
    PRIMARY KEY (island_id, memory_id));

CREATE TABLE IF NOT EXISTS people (
    person_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    relationship TEXT, notes TEXT, affect_profile_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS aelen_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS session_compliance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    boot_ran    INTEGER DEFAULT 0,
    boot_at     TEXT,
    timestamp_logged INTEGER DEFAULT 0,
    exchanges_stored INTEGER DEFAULT 0,
    last_exchange_at TEXT,
    heartbeats_sent  INTEGER DEFAULT 0,
    tool_calls_total INTEGER DEFAULT 0,
    ended_at    TEXT,
    compliance_score REAL DEFAULT 0.0,
    notes       TEXT DEFAULT '');

CREATE INDEX IF NOT EXISTS idx_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_source ON memories(source_type);
CREATE INDEX IF NOT EXISTS idx_core ON memories(is_core);
CREATE INDEX IF NOT EXISTS idx_affect ON memory_affect(memory_id);
CREATE INDEX IF NOT EXISTS idx_efrom ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_eto ON edges(to_id);
"""


def ts(days_ago=0, hours_ago=0):
    """ISO timestamp N days/hours before NOW."""
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat()


# Each tuple is everything the dashboard panels care about for one memory.
# (raw_text, memory_type, source_type, proposed_by, status, days_ago,
#  consent_level, counterweight_type, is_core, valence, arousal, emotion_label)
MEMORIES = [
    # Skill-acquisition arc: learning to bake bread (10 entries spanning 30 days)
    ("Started baking bread today. Used the no-knead recipe. The dough was sticky and I wasn't sure if that was right.", "experience", "teaching", "user", "durable", 30, "low", "neutral", 0, 0.2, 0.4, "curious"),
    ("First loaf came out dense. Crumb was tight and the crust was pale. Probably under-proofed.", "experience", "teaching", "user", "durable", 28, "low", "neutral", 0, -0.3, 0.3, "disappointed"),
    ("User wants to understand bread structure better before trying again. Suggested reading about gluten development.", "teaching_moment", "inference", "assistant", "durable", 27, "low", "supportive", 0, 0.1, 0.2, "supportive"),
    ("Tried a longer cold ferment, 18 hours in the fridge. The dough was much more relaxed and easier to shape.", "experience", "teaching", "user", "durable", 22, "low", "neutral", 0, 0.4, 0.4, "encouraged"),
    ("Second loaf was a step better, more open crumb, better oven spring. User noted the crust still didn't blister.", "breakthrough", "teaching", "user", "durable", 21, "low", "neutral", 0, 0.5, 0.5, "proud"),
    ("Hypothesis: blistering requires more humidity in the oven. User plans to try ice cubes in a tray next bake.", "insight", "inference", "assistant", "durable", 20, "low", "neutral", 0, 0.2, 0.3, "analytical"),
    ("Third loaf with the ice-cube steam method. Crust blistered. User said it was the best loaf yet.", "breakthrough", "teaching", "user", "durable", 14, "medium", "neutral", 1, 0.7, 0.5, "delighted"),
    ("Pattern: user learns fastest when they can run a single-variable experiment between bakes. Don't change two things at once.", "pattern", "inference", "assistant", "durable", 13, "low", "neutral", 0, 0.3, 0.2, "observant"),
    ("User asked about sourdough starter. Wants to graduate from commercial yeast. Mentioned it'd be a multi-week project.", "exchange", "exchange", "user", "durable", 7, "low", "neutral", 0, 0.4, 0.3, "curious"),
    ("Began feeding a sourdough starter today. Day 1 of what will probably be a 7-10 day process.", "experience", "teaching", "user", "durable", 6, "low", "neutral", 0, 0.3, 0.4, "patient"),

    # Garden arc: longitudinal continuity over a longer span (8 entries, 60+ days)
    ("Planted tomato starts. Three varieties: a paste, a slicer, and a cherry. Bed is south-facing.", "experience", "teaching", "user", "durable", 60, "low", "neutral", 0, 0.5, 0.4, "hopeful"),
    ("First flowers on the cherry tomatoes. Earlier than expected for the variety.", "experience", "teaching", "user", "durable", 45, "low", "neutral", 0, 0.4, 0.3, "encouraged"),
    ("Slicer tomatoes showing signs of blight. Lower leaves yellowing with brown spots.", "experience", "teaching", "user", "durable", 30, "low", "neutral", 0, -0.5, 0.5, "concerned"),
    ("Removed affected leaves on the slicer and increased air flow with a stake. Watching for further spread.", "experience", "teaching", "user", "durable", 28, "low", "neutral", 0, 0.0, 0.4, "determined"),
    ("Cherry tomatoes are producing heavily. User mentioned they're harvesting daily now.", "experience", "teaching", "user", "durable", 14, "low", "neutral", 0, 0.6, 0.4, "abundant"),
    ("Pattern: in this bed, the cherry variety has been resilient across two seasons, the slicer struggles both years.", "pattern", "inference", "assistant", "durable", 10, "low", "neutral", 0, 0.2, 0.2, "observant"),
    ("User considering replacing the slicer variety next year. Asked for suggestions for blight-resistant cultivars.", "exchange", "exchange", "user", "durable", 8, "low", "neutral", 0, 0.2, 0.3, "planning"),
    ("Suggested investigating 'Defiant' or 'Iron Lady' for blight resistance. User said they'd research before next season.", "teaching_moment", "inference", "assistant", "durable", 8, "low", "supportive", 0, 0.3, 0.3, "supportive"),

    # Side-project arc: technical patterns (8 entries, recent)
    ("User started a side project, a static site generator for personal notes. Wants to keep it small.", "experience", "teaching", "user", "durable", 21, "low", "neutral", 0, 0.4, 0.5, "energized"),
    ("User got frustrated when the build broke after adding a new template. Reverted the change.", "experience", "teaching", "user", "durable", 18, "low", "anti-spiral", 0, -0.4, 0.6, "frustrated"),
    ("Suggested writing a smoke test for the build step before adding the next template. User agreed.", "teaching_moment", "inference", "assistant", "durable", 18, "low", "grounding", 0, 0.2, 0.3, "supportive"),
    ("First smoke test added. It caught the issue immediately when user tried the new template again.", "breakthrough", "teaching", "user", "durable", 15, "low", "neutral", 0, 0.6, 0.4, "satisfied"),
    ("User wants to add a search feature but is worried about scope creep. Asked for an honest take.", "exchange", "exchange", "user", "durable", 10, "medium", "neutral", 0, 0.0, 0.3, "deliberating"),
    ("Recommended deferring the search feature until the site has more than 50 entries. Premature optimization.", "teaching_moment", "inference", "assistant", "durable", 10, "low", "grounding", 0, 0.1, 0.2, "measured"),
    ("Pattern: user is more productive when they make one architectural change per day and let it settle.", "pattern", "inference", "assistant", "durable", 5, "low", "neutral", 0, 0.3, 0.2, "observant"),
    ("Site now has 18 entries. User said writing the entries is more fun than building the generator.", "exchange", "exchange", "user", "durable", 2, "low", "neutral", 0, 0.4, 0.3, "content"),

    # Identity / relational layer (6 entries)
    ("User prefers to be asked one clarifying question before a long suggestion, rather than getting the whole plan upfront.", "identity", "teaching", "user", "durable", 50, "low", "neutral", 1, 0.0, 0.2, "neutral"),
    ("User's working style is iterative. They want partial drafts to react to, not a fully-baked proposal.", "identity", "teaching", "user", "durable", 48, "low", "neutral", 1, 0.0, 0.2, "neutral"),
    ("User is comfortable with direct technical feedback. Soften only when the topic is non-technical.", "identity", "teaching", "user", "durable", 47, "low", "neutral", 1, 0.0, 0.2, "neutral"),
    ("User mentioned their housemate Alex is a graphic designer who's been helping with the site's visual style.", "relationship", "teaching", "user", "durable", 19, "low", "neutral", 0, 0.3, 0.3, "warm"),
    ("Alex suggested swapping the serif body font for a humanist sans. User agreed it reads better.", "exchange", "exchange", "user", "durable", 17, "low", "neutral", 0, 0.3, 0.3, "collaborative"),
    ("User trusts Alex's eye for typography but tends to over-rely on their input. Worth keeping a mental note.", "pattern", "inference", "assistant", "durable", 16, "medium", "neutral", 0, -0.1, 0.2, "thoughtful"),

    # Corrections (3 entries, exercises the corrections panel)
    ("Correction: previously logged that the bread recipe was Tartine-style, user clarified it's actually no-knead from a different source. Updating attribution.", "correction", "teaching", "user", "durable", 25, "low", "neutral", 0, 0.0, 0.2, "neutral"),
    ("Correction: misread user's note as 'cherry tomatoes are struggling' when in fact it's the slicer struggling and the cherry doing well. Reversed in memory.", "correction", "teaching", "user", "durable", 29, "low", "neutral", 0, 0.0, 0.3, "neutral"),
    ("Correction: said user uses a stand mixer, but they actually mix by hand. Adjusting the kitchen-tools profile.", "correction", "teaching", "user", "durable", 12, "low", "neutral", 0, 0.0, 0.2, "neutral"),

    # Inference-pipeline-only (dream from the sleep daemon)
    ("Sleep cycle reflection: the bread/garden arcs share a structural pattern, both improve when the user runs single-variable experiments. The site project replicates this with the smoke-test discipline.", "dream", "inference", "sleep_daemon", "durable", 9, "low", "neutral", 0, 0.3, 0.2, "reflective"),
    ("Sleep cycle reflection: high-confidence inferences in this corpus cluster around process patterns, not factual claims. Lower confidence on factual claims is appropriate.", "dream", "inference", "sleep_daemon", "durable", 4, "low", "neutral", 0, 0.2, 0.1, "calm"),

    # Promises / commitments (3 entries)
    ("User asked me to remind them to check the sourdough starter at 7am tomorrow.", "promise", "teaching", "user", "durable", 1, "medium", "neutral", 0, 0.2, 0.4, "committed"),
    ("User asked me to flag if I ever drift toward sycophancy in technical reviews. They prefer the friction.", "promise", "teaching", "user", "durable", 40, "low", "neutral", 1, 0.0, 0.3, "committed"),
    ("User asked me to not store medical or financial details unless explicitly asked. Honoring this as a hard rule.", "promise", "teaching", "user", "durable", 55, "high", "neutral", 1, 0.0, 0.2, "committed"),

    # Provisional inferences (the test for needs_user_confirmation)
    ("Provisional inference: user may be drawn to projects with long feedback loops (bread, gardens, longitudinal note-keeping). Not yet confirmed.", "insight", "inference", "assistant", "provisional", 6, "low", "neutral", 0, 0.2, 0.2, "tentative"),
    ("Provisional inference: user's frustration tolerance is higher with their own code than with external tooling. Awaiting confirmation.", "insight", "inference", "assistant", "provisional", 3, "low", "neutral", 0, 0.1, 0.3, "tentative"),

    # Expired / rejected (the test for the lifecycle panels)
    ("Expired inference: user might want morning routine suggestions. Never confirmed; TTL passed.", "insight", "inference", "assistant", "expired", 90, "low", "neutral", 0, 0.0, 0.1, "neutral"),
    ("Rejected: previously inferred user dislikes long-form replies. User corrected. They like long when there's substance, not when there's padding.", "pattern", "inference", "assistant", "rejected", 35, "low", "neutral", 0, -0.1, 0.2, "corrected"),

    # Boot-context grounding entries (so the ring has something on warm start)
    ("User's current focus: shipping the site generator and finishing the sourdough starter cycle.", "exchange", "exchange", "user", "durable", 0, "low", "neutral", 0, 0.3, 0.3, "focused"),
    ("Currently: between baking cycles, garden in late-summer mode, side project on a steady pace.", "exchange", "exchange", "user", "durable", 0, "low", "neutral", 0, 0.4, 0.2, "settled"),
]


ISLANDS = [
    ("Kitchen / Bread Project", "Skill-acquisition arc; single-variable experimentation pattern is most visible here.", 0.78, [0, 1, 4, 6, 7]),
    ("Garden / Longitudinal", "Multi-season patterns; resilience of cherry variety vs. slicer struggle.", 0.66, [10, 11, 12, 13, 14, 15]),
    ("Side Project / Build Discipline", "Smoke-test pattern; one-architectural-change-per-day rhythm.", 0.71, [20, 23, 25, 26]),
    ("Identity Anchors", "User's working preferences; treated as durable teachings.", 0.85, [28, 29, 30]),
    ("Relational / Alex", "Housemate Alex's input on the site's visual style; over-reliance flag.", 0.42, [31, 32, 33]),
]


def db_is_empty(con):
    try:
        n = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return n == 0
    except sqlite3.OperationalError:
        return True


def main():
    db_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(db_dir, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA_SQL)
    con.commit()

    if not db_is_empty(con):
        print(f"[seed_demo] {DB_PATH} already has memories, skipping seed.")
        return 0

    print(f"[seed_demo] populating {DB_PATH} with {len(MEMORIES)} synthetic memories.")
    c = con.cursor()
    memory_ids = []

    for raw, mtype, src, prop, status, days, consent, cw, core, val, arou, emo in MEMORIES:
        created = ts(days_ago=days)
        needs_conf = 1 if status == "provisional" else 0
        c.execute("""
            INSERT INTO memories (raw_text, memory_type, source_type, proposed_by, status,
                                  consent_level, counterweight_type, is_core,
                                  needs_user_confirmation, created_at, updated_at,
                                  confidence, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (raw, mtype, src, prop, status, consent, cw, core, needs_conf,
              created, created, 0.8 if src == "inference" else 1.0,
              max(0, 30 - days)))
        mid = c.lastrowid
        memory_ids.append(mid)

        emotion_json = json.dumps({emo: 0.7})
        c.execute("""
            INSERT INTO memory_affect (memory_id, valence, arousal, dominance, emotion_json, confidence, computed_at, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (mid, val, arou, 0.0, emotion_json, 0.6, created, "manual"))

    # Edges: a handful of resonances and one contradiction
    edge_pairs = [
        (5, 7, "elaboration", 0.78, "The blistering hypothesis (5) was confirmed by the steam-method bake (7)."),
        (7, 4, "resonance", 0.55, "Both are step-changes in bread quality; one was an open crumb, the other was crust."),
        (25, 23, "elaboration", 0.72, "The 'one change per day' pattern (25) builds on the smoke-test discipline (23)."),
        (32, 33, "resonance", 0.60, "Alex's typography suggestion and the over-reliance pattern reference the same relationship."),
        (37, 6, "contradiction", 0.5, "The earlier dream-cycle reflection generalizes a pattern that a specific bake (6) was the local instance of."),
    ]
    for from_idx, to_idx, etype, weight, rationale in edge_pairs:
        if from_idx < len(memory_ids) and to_idx < len(memory_ids):
            try:
                c.execute("""
                    INSERT INTO edges (from_id, to_id, edge_type, weight, rationale, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (memory_ids[from_idx], memory_ids[to_idx], etype, weight, rationale, ts(days_ago=5)))
            except sqlite3.IntegrityError:
                pass

    # Islands
    for name, desc, strength, member_indices in ISLANDS:
        c.execute("""
            INSERT INTO islands (name, description, strength, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (name, desc, strength, ts(days_ago=30), ts(days_ago=1)))
        island_id = c.lastrowid
        for i in member_indices:
            if i < len(memory_ids):
                c.execute("""
                    INSERT INTO island_members (island_id, memory_id, strength)
                    VALUES (?, ?, ?)
                """, (island_id, memory_ids[i], 0.7))

    # Ring: the 8 most recent durable items
    ring_indices = [-1, -2, -3, -4, 6, 23, 25, 28]
    for slot, idx in enumerate(ring_indices):
        if abs(idx) < len(memory_ids):
            try:
                c.execute("""
                    INSERT INTO ring (slot, memory_id, activation, last_activated_at, reason)
                    VALUES (?, ?, ?, ?, ?)
                """, (slot, memory_ids[idx], 0.9 - slot * 0.05, ts(hours_ago=slot),
                      "warm-start grounding" if slot < 4 else "thematic anchor"))
            except sqlite3.IntegrityError:
                pass

    # People
    people = [
        ("Alex", "housemate", "Graphic designer; collaborator on the site's visual style. Trusted eye for typography."),
    ]
    for name, rel, notes in people:
        c.execute("""
            INSERT INTO people (name, relationship, notes, affect_profile_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, rel, notes, json.dumps({"warmth": 0.7}), ts(days_ago=60), ts(days_ago=5)))

    # aelen_state, gives the emotional-state panel a recent mood reading
    c.execute("""
        INSERT INTO aelen_state (key, value, updated_at)
        VALUES (?, ?, ?)
    """, ("emotional_state", "settled, mid-arc", ts(hours_ago=2)))

    # session_compliance, a few historical sessions so the compliance panel has shape
    sessions = [
        ("demo-session-001", 14, 1, 12, 14, 0.92),
        ("demo-session-002", 10, 1, 9, 11, 0.88),
        ("demo-session-003",  7, 1, 15, 19, 0.95),
        ("demo-session-004",  3, 1,  8,  9, 0.86),
        ("demo-session-005",  1, 1, 11, 13, 0.91),
    ]
    for sid, days_ago, boot, exch, tools, score in sessions:
        c.execute("""
            INSERT INTO session_compliance (session_id, started_at, boot_ran, boot_at,
                                            exchanges_stored, last_exchange_at,
                                            tool_calls_total, ended_at, compliance_score, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, ts(days_ago=days_ago), boot, ts(days_ago=days_ago, hours_ago=-1),
              exch, ts(days_ago=days_ago, hours_ago=-3),
              tools, ts(days_ago=days_ago, hours_ago=-4), score,
              "demo session"))

    con.commit()
    con.close()
    print(f"[seed_demo] done, {len(MEMORIES)} memories, {len(ISLANDS)} islands, "
          f"{len(edge_pairs)} edges, {len(sessions)} compliance rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
