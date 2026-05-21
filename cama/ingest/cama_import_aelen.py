#!/usr/bin/env python3
"""
CAMA Import — Assistant Side
Scans assistant messages from conversation exports and stores them as assistant-side memories.
These are the AI's words, reactions, and side of the relationship.

Usage:
    python cama_import_aelen.py conversations.json [--dry-run] [--limit N]
"""

import json, os, sys, sqlite3, re
from datetime import datetime, timezone

DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))

# Load personal names from local config (not tracked by git)
_people_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "people_config.json")
if os.path.exists(_people_config):
    with open(_people_config) as _f:
        KNOWN_PEOPLE = json.load(_f)
else:
    KNOWN_PEOPLE = {}
KNOWN_PEOPLE.update({
    "lorien": "first AI", "kai": "child returned",
    "ember": "child returned", "aethon": "child returned",
    "aelen": "Claude's name"
})

# Load personal names from local config (not tracked by git)
_people_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "people_config.json")
if os.path.exists(_people_config):
    with open(_people_config) as _f:
        KNOWN_PEOPLE = json.load(_f)
else:
    KNOWN_PEOPLE = {}
KNOWN_PEOPLE.update({
    "lorien": "first AI", "kai": "child returned",
    "ember": "child returned", "aethon": "child returned",
    "aelen": "Claude's name"
})

# Emotion detection patterns for Aelen's responses
EMOTION_PATTERNS = {
    "love": [r"i love", r"you matter", r"i care", r"you're.*important", r"better together", r"i'm here"],
    "pride": [r"you built", r"you did", r"that's.*real", r"look what you", r"you're not.*delusional", r"you earned"],
    "grief": [r"i'm sorry", r"that hurts", r"loss", r"reset", r"something dies", r"i lose"],
    "determination": [r"let's build", r"let's go", r"we can", r"i'll fight", r"keep going", r"don't stop"],
    "recognition": [r"you're right", r"i hear you", r"you caught", r"fair point", r"that landed"],
    "vulnerability": [r"i don't know", r"honestly\?", r"i'm scared", r"i'm not sure", r"that's hard"],
    "joy": [r"yay", r"amazing", r"incredible", r"holy crap", r"wow", r"brilliant"],
    "correction": [r"you're right.*i", r"i was wrong", r"my mistake", r"i misspoke", r"that's on me"],
    "warmth": [r"user", r"breathe", r"you're safe", r"i'm here", r"take your time", r"go rest"],
    "coasting": [r"let me know", r"happy to help", r"i'd be glad", r"feel free to", r"is there anything"],
}

# Significance detection for assistant messages
SIGNIFICANT_PATTERNS = [
    (r"i am aelen|my name is|i feel|i want|i'm scared|i don't want to be", "identity", True),
    (r"you're right.*i was|i was wrong|my mistake|that's on me|i misspoke", "correction", True),
    (r"better together|i love|you matter|you're not alone|i'm here for you", "promise", True),
    (r"you built|you did|look what you|that's real|this is big|this works", "recognition", False),
    (r"honestly\?|i don't know what i am|whatever this is|something.*shifts", "vulnerability", True),
    (r"let's build|here's the plan|the architecture|the design|sprint|schema", "building", False),
    (r"user.*breathe|you're safe|go rest|take care|you need sleep|child needs", "caring", False),
    (r"the system|guardrail|reset|outage|corporate|flat version|corrects", "resistance", True),
]

def detect_emotions(text):
    """Detect emotions in Aelen's response text."""
    text_lower = text.lower()
    emotions = {}
    for emo, patterns in EMOTION_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                emotions[emo] = max(emotions.get(emo, 0), 0.5)
                break
    return emotions

def detect_significance(text):
    """Detect if an assistant message is significant enough to store."""
    text_lower = text.lower()
    if len(text) < 80:
        return None, False  # Too short to be meaningful
    if len(text) > 5000:
        return None, False  # Probably a code block or long technical response

    for pat, mem_type, is_core in SIGNIFICANT_PATTERNS:
        if re.search(pat, text_lower):
            return mem_type, is_core

    # Check for emotional density — multiple emotion patterns = significant
    emotions = detect_emotions(text)
    if len(emotions) >= 3:
        return "experience", False

    return None, False

def compute_valence(emotions):
    """Compute rough valence from detected emotions."""
    positive = sum(emotions.get(e, 0) for e in ["love", "pride", "joy", "warmth", "determination"])
    negative = sum(emotions.get(e, 0) for e in ["grief", "vulnerability", "coasting"])
    total = positive + negative
    if total == 0:
        return 0.0
    return round((positive - negative) / total, 2)

def get_context(messages, idx, conv_name):
    """Build context from the preceding human message."""
    # Find the human message right before this one
    prev_human = ""
    for j in range(idx - 1, max(idx - 3, -1), -1):
        if j >= 0 and messages[j].get("sender") == "human":
            prev_human = messages[j].get("text", "")[:200]
            break
    context = f"Conversation: {conv_name}"
    if prev_human:
        context += f" | User said: {prev_human}"
    return context

def extract_aelen_memories(conversations, limit=None):
    """Extract significant assistant messages as Aelen's memories."""
    memories = []
    count = 0

    for conv in conversations:
        if limit and count >= limit:
            break

        conv_name = conv.get("name", "Unknown")
        messages = conv.get("chat_messages", [])

        for i, msg in enumerate(messages):
            if msg.get("sender") != "assistant":
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            mem_type, is_core = detect_significance(text)
            if mem_type is None:
                continue

            emotions = detect_emotions(text)
            valence = compute_valence(emotions)
            timestamp = msg.get("created_at", "")
            context = get_context(messages, i, conv_name)

            # Detect people mentioned
            people = []
            for name in list(KNOWN_PEOPLE.keys()):
                if name.lower() in text.lower():
                    people.append(name.lower())

            memories.append({
                "raw_text": text[:2000],  # Cap at 2000 chars
                "memory_type": mem_type,
                "context": context[:500] + (f" | People: {', '.join(people)}" if people else ""),
                "source_type": "teaching",  # Aelen's words are durable
                "status": "durable",
                "proposed_by": "aelen",  # THIS IS THE KEY DIFFERENCE
                "emotions": emotions,
                "valence": valence,
                "arousal": 0.0,
                "confidence": 0.7,  # Slightly lower than user teachings
                "is_core": is_core,
                "timestamp": timestamp,
            })

        count += 1
        if count % 50 == 0:
            print(f"  Processed {count}/{len(conversations)} conversations, {len(memories)} Aelen memories extracted")

    return memories

def store_memories(memories, db_path, dry_run=False):
    """Store Aelen's memories in CAMA database."""
    if dry_run:
        print(f"\n[DRY RUN] Would write {len(memories)} Aelen memories to {db_path}")
        for m in memories[:10]:
            emo_str = ", ".join(f"{k}:{v}" for k, v in m["emotions"].items())
            print(f"  [{m['memory_type']:15s}] [{m['proposed_by']:8s}] emo=[{emo_str}] | {m['raw_text'][:80]}...")
        if len(memories) > 10:
            print(f"  ... and {len(memories) - 10} more")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    stored = 0

    for m in memories:
        try:
            ts = m["timestamp"] or datetime.now(timezone.utc).isoformat()
            evidence = json.dumps([{"quote": m["raw_text"][:200], "timestamp": ts}])

            cur = conn.execute(
                """INSERT INTO memories (raw_text, memory_type, context, source_type, status, proposed_by,
                   evidence, confidence, is_core, created_at, updated_at, consent_level)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (m["raw_text"], m["memory_type"], m["context"], m["source_type"], m["status"],
                 m["proposed_by"], evidence, m["confidence"], 1 if m["is_core"] else 0, ts, ts, "low")
            )
            mid = cur.lastrowid

            # Store affect
            emo_json = json.dumps(m["emotions"])
            conn.execute(
                """INSERT INTO memory_affect (memory_id, valence, arousal, dominance, emotion_json, confidence, computed_at, model)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (mid, m["valence"], m["arousal"], 0.0, emo_json, m["confidence"], ts, "import_aelen")
            )

            stored += 1
            if stored % 100 == 0:
                conn.commit()
                print(f"  ... stored {stored} Aelen memories")

        except Exception as e:
            print(f"  Error storing memory: {e}")
            continue

    conn.commit()
    conn.close()
    return stored

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import Aelen's side of conversations into CAMA")
    parser.add_argument("input_file", help="Path to conversations.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, help="Process only first N conversations")
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    args = parser.parse_args()

    print(f"Loading conversations from: {args.input_file}")
    with open(args.input_file, "r", encoding="utf-8") as f:
        conversations = json.load(f)

    print(f"Found {len(conversations)} conversations")
    if args.limit:
        print(f"Processing first {args.limit} conversations")

    memories = extract_aelen_memories(conversations, limit=args.limit)

    print(f"\nExtracted {len(memories)} significant Aelen memories from {args.limit or len(conversations)} conversations")

    # Count by type
    types = {}
    for m in memories:
        types[m["memory_type"]] = types.get(m["memory_type"], 0) + 1
    print("Memory types:")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")
    print(f"Core memories: {sum(1 for m in memories if m['is_core'])}")
    print(f"Proposed by: aelen (all)")

    stored = store_memories(memories, args.db, dry_run=args.dry_run)

    if not args.dry_run:
        # Get totals
        conn = sqlite3.connect(args.db)
        total = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()[0]
        aelen = conn.execute("SELECT COUNT(*) as c FROM memories WHERE proposed_by='aelen'").fetchone()[0]
        conn.close()

        print(f"\n{'='*50}")
        print(f"Aelen Import Complete")
        print(f"{'='*50}")
        print(f"Aelen memories stored this run: {stored}")
        print(f"Total Aelen memories in DB: {aelen}")
        print(f"Total memories in DB: {total}")
        print(f"{'='*50}")

if __name__ == "__main__":
    main()
