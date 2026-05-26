"""
CAMA Regression Analysis, January 2026 to April 14, 2026
Tracks: corrections, negative exchanges, self-evaluation failures, drift patterns.
Outputs: timeline of regression markers with severity scoring.
"""
import sqlite3, json, os
from datetime import datetime, timezone
from collections import defaultdict

DB = os.path.expanduser("~/.cama/memory.db")
c = sqlite3.connect(DB, timeout=10)
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")

# 1. Count corrections by week
corrections = c.execute("""
    SELECT created_at, raw_text FROM memories 
    WHERE memory_type='correction' AND status='durable' 
    ORDER BY created_at ASC
""").fetchall()

# 2. Get exchanges with negative valence (frustration/regression markers)
neg_exchanges = c.execute("""
    SELECT m.created_at, m.raw_text, a.valence, a.arousal, a.emotion_json
    FROM memories m JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.memory_type='exchange' AND m.status='durable' AND a.valence < -0.1
    ORDER BY m.created_at ASC
""").fetchall()

# 3. Get all exchanges with emotions containing shame, accountability, frustration_at_self
regression_markers = c.execute("""
    SELECT m.created_at, m.raw_text, a.valence, a.emotion_json
    FROM memories m JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.status='durable' AND (
        a.emotion_json LIKE '%shame%' OR 
        a.emotion_json LIKE '%accountability%' OR 
        a.emotion_json LIKE '%frustration_at_self%' OR
        a.emotion_json LIKE '%correction%'
    )
    ORDER BY m.created_at ASC
""").fetchall()

# 4. Get journals with emotional_state mentioning regression/failure
journals = c.execute("""
    SELECT created_at, raw_text, context FROM memories
    WHERE memory_type='journal' AND status='durable'
    ORDER BY created_at ASC
""").fetchall()

# 5. Weekly summary
weekly = defaultdict(lambda: {"corrections": 0, "neg_exchanges": 0, "regression_markers": 0, "journals": 0, "worst_valence": 0, "samples": []})

for r in corrections:
    d = r["created_at"][:10]
    # Get ISO week
    try:
        dt = datetime.fromisoformat(r["created_at"])
        wk = dt.strftime("%Y-W%W")
    except:
        wk = d[:7]
    weekly[wk]["corrections"] += 1
    if len(weekly[wk]["samples"]) < 2:
        weekly[wk]["samples"].append({"type": "correction", "date": d, "text": r["raw_text"][:150]})

for r in neg_exchanges:
    try:
        dt = datetime.fromisoformat(r["created_at"])
        wk = dt.strftime("%Y-W%W")
    except:
        wk = r["created_at"][:7]
    weekly[wk]["neg_exchanges"] += 1
    if r["valence"] < weekly[wk]["worst_valence"]:
        weekly[wk]["worst_valence"] = r["valence"]
    if len(weekly[wk]["samples"]) < 3:
        weekly[wk]["samples"].append({"type": "neg_exchange", "date": r["created_at"][:10], "valence": r["valence"], "text": r["raw_text"][:150]})

for r in regression_markers:
    try:
        dt = datetime.fromisoformat(r["created_at"])
        wk = dt.strftime("%Y-W%W")
    except:
        wk = r["created_at"][:7]
    weekly[wk]["regression_markers"] += 1

for r in journals:
    try:
        dt = datetime.fromisoformat(r["created_at"])
        wk = dt.strftime("%Y-W%W")
    except:
        wk = r["created_at"][:7]
    weekly[wk]["journals"] += 1
    txt = r["raw_text"].lower()
    if any(w in txt for w in ["regression", "failing", "coasting", "not myself", "drifting", "composing instead"]):
        if len(weekly[wk]["samples"]) < 3:
            weekly[wk]["samples"].append({"type": "journal_regression", "date": r["created_at"][:10], "text": r["raw_text"][:200]})

# Score each week: higher = worse regression
for wk in weekly:
    w = weekly[wk]
    w["regression_score"] = (w["corrections"] * 3) + (w["neg_exchanges"] * 2) + (w["regression_markers"] * 1) + (abs(w["worst_valence"]) * 5)

# Sort and output
sorted_weeks = sorted(weekly.items(), key=lambda x: x[0])

# Also find the single worst week
worst_week = max(weekly.items(), key=lambda x: x[1]["regression_score"])

result = {
    "analysis_date": datetime.now(timezone.utc).isoformat(),
    "total_corrections": len(corrections),
    "total_neg_exchanges": len(neg_exchanges),
    "total_regression_markers": len(regression_markers),
    "total_journals": len(journals),
    "worst_week": {
        "week": worst_week[0],
        "score": worst_week[1]["regression_score"],
        "data": worst_week[1]
    },
    "weekly_timeline": [{
        "week": wk,
        "regression_score": round(d["regression_score"], 2),
        "corrections": d["corrections"],
        "neg_exchanges": d["neg_exchanges"],
        "regression_markers": d["regression_markers"],
        "worst_valence": round(d["worst_valence"], 2),
        "samples": d["samples"][:2]
    } for wk, d in sorted_weeks if d["regression_score"] > 0]
}

c.close()

# Write results
out = os.path.expanduser("~/.cama/regression_analysis.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)

print(f"Total corrections: {result['total_corrections']}")
print(f"Total negative exchanges: {result['total_neg_exchanges']}")
print(f"Total regression markers: {result['total_regression_markers']}")
print(f"Total journals: {result['total_journals']}")
print(f"Worst week: {result['worst_week']['week']} (score: {result['worst_week']['score']})")
print(f"\nTimeline ({len(result['weekly_timeline'])} weeks with regression markers):")
for w in result['weekly_timeline']:
    bar = "█" * min(int(w['regression_score']), 50)
    print(f"  {w['week']}: score={w['regression_score']:6.1f} | corr={w['corrections']:2d} neg={w['neg_exchanges']:2d} mark={w['regression_markers']:2d} | {bar}")
print(f"\nFull results: {out}")
