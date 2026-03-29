#!/usr/bin/env python3
"""Diagnostic: why is batch 0 producing no edges?"""
import sqlite3, json, math
from collections import defaultdict
from datetime import datetime, timezone

c = sqlite3.connect(r'C:\Users\Angela\.cama\memory.db')
c.row_factory = sqlite3.Row

# Get first 1000 durable by ID (same as v2 offset=0)
rows = c.execute("""
    SELECT m.id, m.raw_text, m.memory_type, m.created_at,
           ma.valence, ma.arousal, ma.emotion_json
    FROM memories m
    LEFT JOIN memory_affect ma ON m.id = ma.memory_id
    WHERE m.status = 'durable'
    ORDER BY m.id ASC
    LIMIT 1000
""").fetchall()

print(f"Batch 0: {len(rows)} memories")
print(f"  ID range: {rows[0]['id']} - {rows[-1]['id']}")
print(f"  Date range: {rows[0]['created_at'][:19]} - {rows[-1]['created_at'][:19]}")

# How many have affect?
has_affect = sum(1 for r in rows if r["emotion_json"])
print(f"  With affect data: {has_affect}")

# Date distribution - how many unique days?
days = set()
for r in rows:
    days.add(r["created_at"][:10])
print(f"  Unique days: {len(days)}")
for d in sorted(days)[:10]:
    count = sum(1 for r in rows if r["created_at"][:10] == d)
    print(f"    {d}: {count} memories")
if len(days) > 10:
    print(f"    ... and {len(days)-10} more days")

# Emotion distribution
emotion_groups = defaultdict(list)
no_emotion = 0
for r in rows:
    emotions = json.loads(r["emotion_json"] or "{}")
    if emotions:
        dominant = max(emotions, key=emotions.get)
        emotion_groups[dominant].append(r)
    else:
        no_emotion += 1

print(f"\n  No emotion data: {no_emotion}")
print(f"  Emotion clusters:")
for emo, mems in sorted(emotion_groups.items(), key=lambda x: -len(x[1])):
    print(f"    {emo}: {len(mems)} memories")

# Now simulate edge creation to find WHERE it's failing
print("\n=== EDGE CREATION SIMULATION ===")
reasons = {"already_exists": 0, "same_day": 0, "too_distant": 0, "no_emotions": 0, "would_create": 0}

existing_edges = set()
for row in c.execute("SELECT from_id, to_id FROM edges").fetchall():
    existing_edges.add((row["from_id"], row["to_id"]))
    existing_edges.add((row["to_id"], row["from_id"]))

comparisons = 0
for emotion, mems in emotion_groups.items():
    if len(mems) < 2:
        continue
    for i in range(len(mems)):
        for j in range(i + 1, min(i + 8, len(mems))):
            comparisons += 1
            m1, m2 = mems[i], mems[j]
            
            if (m1["id"], m2["id"]) in existing_edges:
                reasons["already_exists"] += 1
                continue
            
            try:
                t1 = datetime.fromisoformat(m1["created_at"])
                t2 = datetime.fromisoformat(m2["created_at"])
            except:
                t1 = t2 = datetime.now(timezone.utc)
            
            if abs((t1 - t2).total_seconds()) < 86400:
                reasons["same_day"] += 1
                continue
            
            e1 = json.loads(m1["emotion_json"] or "{}")
            e2 = json.loads(m2["emotion_json"] or "{}")
            all_emotions = set(list(e1.keys()) + list(e2.keys()))
            if not all_emotions:
                reasons["no_emotions"] += 1
                continue
            
            dist = math.sqrt(sum((e1.get(e, 0) - e2.get(e, 0))**2 for e in all_emotions) / len(all_emotions))
            
            if dist >= 0.4:
                reasons["too_distant"] += 1
            else:
                reasons["would_create"] += 1

print(f"  Total comparisons: {comparisons}")
for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
    pct = round(count / max(1, comparisons) * 100, 1)
    print(f"  {reason}: {count} ({pct}%)")

# Sample some "too_distant" pairs to see what the distances look like
print("\n=== DISTANCE DISTRIBUTION (sampled) ===")
distances = []
for emotion, mems in emotion_groups.items():
    if len(mems) < 2:
        continue
    for i in range(min(5, len(mems))):
        for j in range(i+1, min(i+3, len(mems))):
            m1, m2 = mems[i], mems[j]
            e1 = json.loads(m1["emotion_json"] or "{}")
            e2 = json.loads(m2["emotion_json"] or "{}")
            all_e = set(list(e1.keys()) + list(e2.keys()))
            if all_e:
                dist = math.sqrt(sum((e1.get(e, 0) - e2.get(e, 0))**2 for e in all_e) / len(all_e))
                distances.append(dist)

if distances:
    distances.sort()
    print(f"  Min: {distances[0]:.4f}")
    print(f"  25th pct: {distances[len(distances)//4]:.4f}")
    print(f"  Median: {distances[len(distances)//2]:.4f}")
    print(f"  75th pct: {distances[3*len(distances)//4]:.4f}")
    print(f"  Max: {distances[-1]:.4f}")
    print(f"  Under 0.3: {sum(1 for d in distances if d < 0.3)}")
    print(f"  Under 0.4: {sum(1 for d in distances if d < 0.4)}")
    print(f"  Under 0.5: {sum(1 for d in distances if d < 0.5)}")

c.close()
