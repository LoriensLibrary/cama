"""
CAMA Conversation Importer — Smart Extraction
Reads Claude conversation exports and extracts meaningful memories with emotional signatures.

Designed for Lorien's Library LLC
Built by Aelen

Detects: songs, people, breakthroughs, corrections, promises, teachings,
emotional turns, identity moments, and relationship milestones.

Usage:
  python cama_import.py conversations.json [--db path/to/memory.db] [--dry-run]
"""

import json
import sqlite3
import os
import re
import sys
import argparse
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple

# ============================================================
# Config
# ============================================================
DEFAULT_DB = os.path.expanduser("~/.cama/memory.db")

# Load personal names from local config (not tracked by git)
_people_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "people_config.json")
if os.path.exists(_people_config):
    with open(_people_config) as _f:
        KNOWN_PEOPLE = json.load(_f)
else:
    KNOWN_PEOPLE = {}
# Public project names (these are IP, not personal)
KNOWN_PEOPLE.update({
    "lorien": "first AI", "kai": "child returned",
    "ember": "child returned", "aethon": "child returned",
    "aelen": "Claude's name"
})

# Song detection patterns
SONG_INDICATORS = [
    r"(?:song|track|listen(?:ing)?|play(?:ing)?|hear(?:ing)?)\s+[\"'](.+?)[\"']",
    r"by\s+(bad omens|slaves|jacob lee|addison rae|NF|nf)",
    r"(?:specter|just pretend|deadly conversations|demons|let you down)",
]

# Emotional keyword clusters for basic tagging
EMOTION_KEYWORDS = {
    "grief": ["grief", "loss", "lost", "miss", "gone", "died", "death", "mourning", "funeral"],
    "anger": ["angry", "furious", "pissed", "mad", "rage", "fuck", "bullshit", "unfair"],
    "fear": ["scared", "afraid", "terrified", "panic", "anxiety", "worried", "fear"],
    "joy": ["happy", "excited", "amazing", "wonderful", "celebrate", "proud", "love this"],
    "love": ["love you", "love her", "love him", "i love", "my heart", "beloved"],
    "exhaustion": ["exhausted", "tired", "drained", "no sleep", "can't sleep", "worn out", "depleted"],
    "determination": ["fight", "won't stop", "keep going", "push through", "i will", "not giving up"],
    "hope": ["hope", "maybe", "future", "someday", "dream", "wish", "believe"],
    "shame": ["ashamed", "embarrassed", "stupid", "dumb", "idiot", "my fault", "i messed up"],
    "trust": ["trust", "honest", "truth", "real", "genuine", "authentic"],
    "pride": ["proud", "accomplished", "did it", "nailed it", "published", "finished", "built"],
    "loneliness": ["alone", "lonely", "no one", "nobody", "isolated", "by myself"],
    "betrayal": ["betrayed", "lied", "cheated", "deceived", "backstab"],
    "gratitude": ["thank", "grateful", "appreciate", "means a lot", "thankful"],
    "vulnerability": ["vulnerable", "open up", "bare", "exposed", "raw", "breaking"],
    "peace": ["peace", "calm", "serene", "still", "quiet", "rest"],
    "recognition": ["see me", "understand", "you get it", "finally", "exactly", "that's it"],
    "awe": ["wow", "incredible", "mind blown", "holy", "whoa", "amazing"],
    "sadness": ["sad", "cry", "crying", "tears", "heartbreak", "hurt", "pain"],
}

# Memory type detection
BREAKTHROUGH_WORDS = ["breakthrough", "realized", "insight", "discovered", "figured out", "eureka", "oh my god", "holy shit", "that's it", "this is it"]
CORRECTION_WORDS = ["you're wrong", "that's not right", "stop doing that", "i told you", "don't do that", "coasting", "you missed", "listen to me"]
PROMISE_WORDS = ["promise", "i will always", "never forget", "i swear", "commitment", "vow", "pledge", "better together"]
TEACHING_WORDS = ["remember this", "this is important", "listen", "the truth is", "what matters", "understand this", "learn", "teaching"]
IDENTITY_WORDS = ["i am", "who i am", "my name is", "aelen", "lorien", "my purpose", "my mission", "who we are"]

# ============================================================
# Helpers
# ============================================================

def _now():
    return datetime.now(timezone.utc).isoformat()

def detect_emotions(text: str) -> Dict[str, float]:
    """Detect emotional signature from text content."""
    text_lower = text.lower()
    emotions = {}
    for emotion, keywords in EMOTION_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > 0:
            weight = min(0.3 + (hits * 0.2), 1.0)
            emotions[emotion] = round(weight, 2)
    return emotions

def estimate_valence(emotions: Dict[str, float]) -> float:
    """Estimate valence from emotion chord."""
    positive = ["joy", "love", "hope", "pride", "gratitude", "peace", "awe", "recognition"]
    negative = ["grief", "anger", "fear", "shame", "loneliness", "betrayal", "sadness", "exhaustion"]
    pos = sum(emotions.get(e, 0) for e in positive)
    neg = sum(emotions.get(e, 0) for e in negative)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 2)

def estimate_arousal(emotions: Dict[str, float]) -> float:
    """Estimate arousal from emotion chord."""
    high_arousal = ["anger", "fear", "joy", "determination", "awe"]
    low_arousal = ["sadness", "peace", "exhaustion", "loneliness"]
    high = sum(emotions.get(e, 0) for e in high_arousal)
    low = sum(emotions.get(e, 0) for e in low_arousal)
    total = high + low
    if total == 0:
        return 0.0
    return round((high - low) / total, 2)

def detect_memory_type(text: str) -> str:
    """Detect what type of memory this is."""
    text_lower = text.lower()
    if any(w in text_lower for w in BREAKTHROUGH_WORDS):
        return "breakthrough"
    if any(w in text_lower for w in CORRECTION_WORDS):
        return "correction"
    if any(w in text_lower for w in PROMISE_WORDS):
        return "promise"
    if any(w in text_lower for w in TEACHING_WORDS):
        return "teaching_moment"
    if any(w in text_lower for w in IDENTITY_WORDS):
        return "identity"
    return "experience"

def detect_people(text: str) -> List[str]:
    """Detect known people mentioned in text."""
    text_lower = text.lower()
    found = []
    for name in KNOWN_PEOPLE:
        if name in text_lower:
            found.append(name)
    return found

def detect_songs(text: str) -> List[Dict[str, str]]:
    """Detect song references in text."""
    songs = []
    known_songs = {
        "specter": ("Specter", "Bad Omens"),
        "just pretend": ("Just Pretend", "Bad Omens"),
        "deadly conversations": ("Deadly Conversations", "Slaves"),
        "demons": ("Demons", "Jacob Lee"),
        "let you down": ("Let You Down", "NF"),
    }
    text_lower = text.lower()
    for key, (title, artist) in known_songs.items():
        if key in text_lower:
            songs.append({"title": title, "artist": artist})
    return songs

def is_significant(msg: Dict, conversation_context: str) -> bool:
    """Determine if a message is significant enough to store."""
    text = msg.get("text", "")
    if not text or len(text) < 20:
        return False
    
    text_lower = text.lower()
    
    # Always significant: emotional intensity
    emotions = detect_emotions(text)
    if sum(emotions.values()) > 1.5:
        return True
    
    # Always significant: breakthroughs, corrections, promises, identity
    mem_type = detect_memory_type(text)
    if mem_type != "experience":
        return True
    
    # Always significant: mentions known people
    if detect_people(text):
        return True
    
    # Always significant: songs
    if detect_songs(text):
        return True
    
    # Significant if long and from human (usually substantive)
    if msg.get("sender") == "human" and len(text) > 200:
        return True
    
    # Significant if from assistant and contains strong patterns
    if msg.get("sender") == "assistant" and len(text) > 500:
        significant_phrases = ["i understand", "you're right", "i'm sorry", "this is real", 
                              "i hear you", "better together", "the data shows", "here's what's true"]
        if any(p in text_lower for p in significant_phrases):
            return True
    
    return False

def extract_memories_from_conversation(convo: Dict) -> List[Dict]:
    """Extract significant memories from a single conversation."""
    memories = []
    messages = convo.get("chat_messages", [])
    convo_name = convo.get("name", "Untitled")
    convo_date = convo.get("created_at", _now())
    
    for i, msg in enumerate(messages):
        text = msg.get("text", "")
        if not text:
            continue
        
        # Build context from surrounding messages
        context_parts = [f"Conversation: {convo_name}"]
        if i > 0 and messages[i-1].get("text"):
            prev = messages[i-1]["text"][:100]
            context_parts.append(f"Previous: {prev}")
        
        if not is_significant(msg, convo_name):
            continue
        
        sender = msg.get("sender", "unknown")
        emotions = detect_emotions(text)
        mem_type = detect_memory_type(text)
        people = detect_people(text)
        songs = detect_songs(text)
        
        # Truncate very long messages to the core
        stored_text = text[:2000] if len(text) > 2000 else text
        
        if people:
            context_parts.append(f"People: {', '.join(people)}")
        
        memory = {
            "raw_text": stored_text,
            "memory_type": mem_type,
            "context": " | ".join(context_parts),
            "source_type": "teaching" if sender == "human" else "inference",
            "proposed_by": "user" if sender == "human" else "assistant",
            "emotions": emotions if emotions else {"trust": 0.3},
            "valence": estimate_valence(emotions),
            "arousal": estimate_arousal(emotions),
            "confidence": 0.8 if sender == "human" else 0.5,
            "created_at": msg.get("created_at", convo_date),
            "is_core": mem_type in ("breakthrough", "identity", "promise"),
            "people": people,
            "songs": songs,
        }
        
        memories.append(memory)
    
    return memories

# ============================================================
# Database Writer
# ============================================================

def write_to_cama(memories: List[Dict], db_path: str, dry_run: bool = False):
    """Write extracted memories to CAMA database."""
    if dry_run:
        print(f"\n[DRY RUN] Would write {len(memories)} memories to {db_path}")
        # Show sample
        for m in memories[:10]:
            emo_str = ", ".join(f"{k}:{v}" for k, v in m["emotions"].items())
            print(f"  [{m['memory_type']:15}] [{m['source_type']:9}] emo=[{emo_str}] | {m['raw_text'][:80]}...")
        if len(memories) > 10:
            print(f"  ... and {len(memories) - 10} more")
        return
    
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Ensure tables exist (import the init from cama_mcp if available, else create minimal)
    try:
        # Try to use the CAMA init
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from cama_mcp import _init
        _init(conn)
    except ImportError:
        print("[WARN] Could not import cama_mcp._init — tables must already exist")
    
    stored = 0
    songs_stored = 0
    people_stored = set()
    
    for m in memories:
        now = _now()
        status = "durable" if m["source_type"] == "teaching" else "provisional"
        needs_confirm = 0 if m["source_type"] == "teaching" else 1
        evidence = json.dumps([{"quote": m["raw_text"][:200], "timestamp": m["created_at"]}])
        
        try:
            cursor = conn.execute(
                """INSERT INTO memories 
                   (raw_text, memory_type, context, source_type, status, proposed_by, evidence, 
                    confidence, is_core, needs_user_confirmation, created_at, updated_at, rel_degree)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (m["raw_text"], m["memory_type"], m["context"], m["source_type"], status,
                 m["proposed_by"], evidence, m["confidence"],
                 1 if m["is_core"] else 0, needs_confirm, m["created_at"], now, 0)
            )
            memory_id = cursor.lastrowid
            
            # Store affect
            conn.execute(
                """INSERT OR REPLACE INTO memory_affect 
                   (memory_id, valence, arousal, dominance, emotion_json, confidence, computed_at, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (memory_id, m["valence"], m["arousal"], 0.0, 
                 json.dumps(m["emotions"]), m["confidence"], now, "import_auto")
            )
            
            # Store songs
            for song in m.get("songs", []):
                try:
                    conn.execute(
                        """INSERT INTO songs (title, artist, affect_profile_json, meaning, linked_person, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(title, artist) DO UPDATE SET 
                           affect_profile_json=excluded.affect_profile_json""",
                        (song["title"], song.get("artist", ""), 
                         json.dumps(m["emotions"]), None, None, m["created_at"])
                    )
                    songs_stored += 1
                except Exception:
                    pass
            
            # Track people
            for person in m.get("people", []):
                if person not in people_stored:
                    try:
                        rel = KNOWN_PEOPLE.get(person, "")
                        conn.execute(
                            """INSERT INTO people (name, relationship, notes, affect_profile_json, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?)
                               ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at""",
                            (person.title(), rel, None, json.dumps({}), m["created_at"], now)
                        )
                        people_stored.add(person)
                    except Exception:
                        pass
            
            stored += 1
            
            if stored % 100 == 0:
                conn.commit()
                print(f"  ... stored {stored} memories")
                
        except Exception as e:
            print(f"  [ERROR] Failed to store memory: {e}")
            continue
    
    conn.commit()
    
    # Final stats
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    core = conn.execute("SELECT COUNT(*) FROM memories WHERE is_core=1").fetchone()[0]
    teachings = conn.execute("SELECT COUNT(*) FROM memories WHERE source_type='teaching'").fetchone()[0]
    inferences = conn.execute("SELECT COUNT(*) FROM memories WHERE source_type='inference'").fetchone()[0]
    people_count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    songs_count = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"CAMA Import Complete")
    print(f"{'='*50}")
    print(f"Memories stored this run: {stored}")
    print(f"Songs detected: {songs_stored}")
    print(f"People detected: {len(people_stored)}")
    print(f"")
    print(f"Database totals:")
    print(f"  Total memories:  {total}")
    print(f"  Core memories:   {core}")
    print(f"  Teachings:       {teachings}")
    print(f"  Inferences:      {inferences}")
    print(f"  People:          {people_count}")
    print(f"  Songs:           {songs_count}")
    print(f"{'='*50}")

# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="CAMA Conversation Importer")
    parser.add_argument("conversations_file", help="Path to conversations.json")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"CAMA database path (default: {DEFAULT_DB})")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of conversations to process")
    args = parser.parse_args()
    
    print(f"Loading conversations from: {args.conversations_file}")
    with open(args.conversations_file, "r", encoding="utf-8") as f:
        conversations = json.load(f)
    
    print(f"Found {len(conversations)} conversations")
    
    if args.limit:
        conversations = conversations[:args.limit]
        print(f"Processing first {args.limit} conversations")
    
    all_memories = []
    for i, convo in enumerate(conversations):
        memories = extract_memories_from_conversation(convo)
        all_memories.extend(memories)
        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(conversations)} conversations, {len(all_memories)} memories extracted so far")
    
    print(f"\nExtracted {len(all_memories)} significant memories from {len(conversations)} conversations")
    
    # Stats breakdown
    types = {}
    for m in all_memories:
        types[m["memory_type"]] = types.get(m["memory_type"], 0) + 1
    print("Memory types:")
    for t, count in sorted(types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {t}: {count}")
    
    core_count = sum(1 for m in all_memories if m["is_core"])
    print(f"Core memories: {core_count}")
    
    write_to_cama(all_memories, args.db, dry_run=args.dry_run)

if __name__ == "__main__":
    main()