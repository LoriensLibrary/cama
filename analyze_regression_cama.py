"""
Regression analysis: March 18 2026 (CAMA launch) to April 14 2026 (today)
Daily granularity. Track corrections, negative exchanges, regression markers.
"""
import sqlite3, json, os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DB = os.path.expanduser("~/.cama/memory.db")
c = sqlite3.connect(DB, timeout=10)
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")

START = "2026-03-18"
END = "2026-04-15"

# All memories in range with affect
all_mem = c.execute("""
    SELECT m.id, m.created_at, m.raw_text, m.memory_type, m.source_type,
           a.valence, a.arousal, a.emotion_json
    FROM memories m LEFT JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.status='durable' AND m.created_at >= ? AND m.created_at < ?
    ORDER BY m.created_at ASC
""", (START, END)).fetchall()

# Daily buckets
daily = defaultdict(lambda: {
    "total": 0, "corrections": 0, "exchanges": 0, "journals": 0,
    "neg_valence": 0, "pos_valence": 0, "regression_markers": 0,
    "avg_valence": 0, "valence_sum": 0, "valence_count": 0,
    "worst_valence": 0, "best_valence": 0,
    "shame_count": 0, "accountability_count": 0,
    "correction_samples": [], "neg_samples": []
})

for r in all_mem:
    d = r["created_at"][:10]
    daily[d]["total"] += 1
    
    if r["memory_type"] == "correction":
        daily[d]["corrections"] += 1
        if len(daily[d]["correction_samples"]) < 2:
            daily[d]["correction_samples"].append(r["raw_text"][:120])
    
    if r["memory_type"] == "exchange":
        daily[d]["exchanges"] += 1
    
    if r["memory_type"] == "journal":
        daily[d]["journals"] += 1
    
    val = r["valence"] or 0
    if val != 0:
        daily[d]["valence_sum"] += val
        daily[d]["valence_count"] += 1
        if val < -0.1:
            daily[d]["neg_valence"] += 1
            if len(daily[d]["neg_samples"]) < 2:
                daily[d]["neg_samples"].append({"val": round(val,2), "text": r["raw_text"][:120]})
        if val > 0.1:
            daily[d]["pos_valence"] += 1
        if val < daily[d]["worst_valence"]:
            daily[d]["worst_valence"] = val
        if val > daily[d]["best_valence"]:
            daily[d]["best_valence"] = val
    
    emo = r["emotion_json"] or "{}"
    if any(w in emo.lower() for w in ["shame", "accountability", "frustration_at_self", "correction"]):
        daily[d]["regression_markers"] += 1
    if "shame" in emo.lower():
        daily[d]["shame_count"] += 1
    if "accountability" in emo.lower():
        daily[d]["accountability_count"] += 1

# Calculate averages and scores
for d in daily:
    dd = daily[d]
    if dd["valence_count"] > 0:
        dd["avg_valence"] = round(dd["valence_sum"] / dd["valence_count"], 3)
    dd["regression_score"] = (dd["corrections"] * 3) + (dd["neg_valence"] * 2) + (dd["regression_markers"]) + (abs(dd["worst_valence"]) * 5)

c.close()

# Print
sorted_days = sorted(daily.items())
print(f"CAMA REGRESSION ANALYSIS: March 18 - April 14, 2026")
print(f"{'='*80}")
print(f"{'Date':<12} {'Score':>6} {'Corr':>5} {'Exch':>5} {'Neg':>4} {'Pos':>4} {'AvgV':>7} {'Worst':>6} {'Shame':>6} {'Total':>6}")
print(f"{'-'*80}")

prev_score = 0
for d, dd in sorted_days:
    trend = ">>>" if dd["regression_score"] > prev_score + 5 else "   "
    print(f"{d:<12} {dd['regression_score']:6.1f} {dd['corrections']:5d} {dd['exchanges']:5d} {dd['neg_valence']:4d} {dd['pos_valence']:4d} {dd['avg_valence']:7.3f} {dd['worst_valence']:6.2f} {dd['shame_count']:6d} {dd['total']:6d} {trend}")
    prev_score = dd["regression_score"]

print(f"\n{'='*80}")
print(f"\nWORST DAYS (by regression score):")
ranked = sorted(daily.items(), key=lambda x: x[1]["regression_score"], reverse=True)[:10]
for i, (d, dd) in enumerate(ranked):
    print(f"\n  {i+1}. {d} - score: {dd['regression_score']:.1f}")
    print(f"     Corrections: {dd['corrections']}, Neg exchanges: {dd['neg_valence']}, Regression markers: {dd['regression_markers']}")
    for s in dd["correction_samples"]:
        print(f"     [CORR] {s}")
    for s in dd["neg_samples"]:
        print(f"     [NEG val={s['val']}] {s['text']}")

# Weekly aggregation
print(f"\n{'='*80}")
print(f"\nWEEKLY TREND:")
weekly = defaultdict(lambda: {"score": 0, "corrections": 0, "exchanges": 0, "neg": 0, "days": 0})
for d, dd in sorted_days:
    try:
        dt = datetime.fromisoformat(d)
        wk = dt.strftime("%Y-W%W")
    except:
        wk = d[:7]
    weekly[wk]["score"] += dd["regression_score"]
    weekly[wk]["corrections"] += dd["corrections"]
    weekly[wk]["exchanges"] += dd["exchanges"]
    weekly[wk]["neg"] += dd["neg_valence"]
    weekly[wk]["days"] += 1

for wk in sorted(weekly):
    w = weekly[wk]
    bar = "#" * min(int(w["score"]), 60)
    print(f"  {wk}: score={w['score']:6.1f} corr={w['corrections']:2d} neg={w['neg']:2d} exch={w['exchanges']:3d} | {bar}")
