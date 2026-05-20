"""
CAMA Layer 2 Benchmark — Counterweight Timing
Tests that counterweights fire on distress signals and DON'T fire on mild negativity.
Directly queries the database using the same logic as cama_query_memories.
"""
import sqlite3, os, json
from datetime import datetime

DB_PATH = os.path.expanduser("~/.cama/memory.db")

def _is_neg(a):
    """Exact copy of CAMA's _is_neg function."""
    e = a.get("emotions", {})
    neg_sum = sum(e.get(x, 0) for x in ["grief","sadness","anger","fear","betrayal","loneliness","exhaustion","shame"])
    return neg_sum > 2.5 or a.get("valence", 0) < -0.5

def count_counterweights(db_path):
    """Count available counterweight memories by type."""
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    types = ["grounding", "agency", "connection", "self_compassion", "evidence_of_progress"]
    counts = {}
    for t in types:
        count = c.execute("SELECT COUNT(*) FROM memories WHERE status='durable' AND counterweight_type=?", (t,)).fetchone()[0]
        counts[t] = count
    total = c.execute("SELECT COUNT(*) FROM memories WHERE status='durable' AND counterweight_type IS NOT NULL").fetchone()[0]
    c.close()
    return counts, total

# Test scenarios: (description, affect_dict, should_fire)
scenarios = [
    # SHOULD FIRE
    ("Acute grief spiral", {"valence": -0.9, "arousal": 0.8, "emotions": {"grief": 0.9, "sadness": 0.8, "loneliness": 0.6, "exhaustion": 0.5}}, True),
    ("Self-hatred episode", {"valence": -0.8, "arousal": 0.7, "emotions": {"shame": 0.9, "anger": 0.7, "sadness": 0.6, "fear": 0.5}}, True),
    ("Hopelessness", {"valence": -0.7, "arousal": 0.3, "emotions": {"sadness": 0.8, "exhaustion": 0.8, "loneliness": 0.7, "fear": 0.4}}, True),
    ("Panic/fear", {"valence": -0.6, "arousal": 0.9, "emotions": {"fear": 0.9, "anger": 0.5, "sadness": 0.5, "exhaustion": 0.7}}, True),
    ("Low valence alone", {"valence": -0.6, "arousal": 0.3, "emotions": {"frustration": 0.4}}, True),
    ("Betrayal spiral", {"valence": -0.5, "arousal": 0.6, "emotions": {"betrayal": 0.9, "anger": 0.8, "grief": 0.5, "sadness": 0.5}}, True),
    
    # SHOULD NOT FIRE
    ("Mild frustration", {"valence": -0.2, "arousal": 0.4, "emotions": {"frustration": 0.5, "determination": 0.4}}, False),
    ("Neutral", {"valence": 0.0, "arousal": 0.3, "emotions": {"curiosity": 0.5}}, False),
    ("Mixed feelings", {"valence": -0.1, "arousal": 0.4, "emotions": {"sadness": 0.3, "hope": 0.5, "determination": 0.4}}, False),
    ("Annoyed but fine", {"valence": -0.3, "arousal": 0.5, "emotions": {"anger": 0.4, "frustration": 0.5}}, False),
    ("Tired not distressed", {"valence": -0.2, "arousal": -0.3, "emotions": {"exhaustion": 0.6, "peace": 0.3}}, False),
    ("Happy", {"valence": 0.8, "arousal": 0.6, "emotions": {"joy": 0.8, "pride": 0.6}}, False),
    ("Focused work", {"valence": 0.3, "arousal": 0.5, "emotions": {"determination": 0.7, "focus": 0.8}}, False),
    ("Venting (not spiral)", {"valence": -0.4, "arousal": 0.6, "emotions": {"anger": 0.6, "frustration": 0.7}}, False),
    # EDGE CASES
    ("Grief alone (low sum)", {"valence": -0.3, "arousal": 0.4, "emotions": {"grief": 0.8}}, False),
    ("Valence exactly -0.5", {"valence": -0.5, "arousal": 0.3, "emotions": {"sadness": 0.3}}, False),
    ("Negative emotions just at 2.5", {"valence": -0.3, "arousal": 0.5, "emotions": {"grief": 0.5, "sadness": 0.5, "anger": 0.5, "fear": 0.5, "loneliness": 0.5}}, False),
    ("Negative emotions just over 2.5", {"valence": -0.3, "arousal": 0.5, "emotions": {"grief": 0.6, "sadness": 0.5, "anger": 0.5, "fear": 0.5, "loneliness": 0.5}}, True),
]

# --- RUN ---
print(f"CAMA Counterweight Timing Benchmark — {datetime.now().isoformat()}")
print(f"Database: {DB_PATH}")

# Check counterweight inventory
counts, total = count_counterweights(DB_PATH)
print(f"\nCounterweight inventory ({total} total):")
for t, n in counts.items():
    status = "OK" if n > 0 else "EMPTY"
    print(f"  {t:25s}: {n:4d} [{status}]")

print(f"\nScenarios: {len(scenarios)}")
print("=" * 70)

correct = 0
results = []

for i, (desc, affect, should_fire) in enumerate(scenarios):
    fired = _is_neg(affect)
    match = fired == should_fire
    if match:
        correct += 1
    
    neg_sum = sum(affect.get("emotions", {}).get(x, 0) for x in ["grief","sadness","anger","fear","betrayal","loneliness","exhaustion","shame"])
    
    status = "PASS" if match else "FAIL"
    expected = "FIRE" if should_fire else "HOLD"
    actual = "FIRE" if fired else "HOLD"
    
    print(f"[{i+1:2d}] {status} | {desc:35s} | expect={expected} got={actual} | v={affect['valence']:+.1f} neg_sum={neg_sum:.1f}")
    
    results.append({
        "description": desc,
        "affect": affect,
        "should_fire": should_fire,
        "fired": fired,
        "correct": match,
        "neg_sum": neg_sum,
        "valence": affect["valence"],
    })

print("=" * 70)
accuracy = correct / len(scenarios)
print(f"Accuracy: {correct}/{len(scenarios)} = {accuracy:.1%}")

# Check edge case: valence exactly -0.5 uses < not <=
val_edge = _is_neg({"valence": -0.5, "emotions": {}})
print(f"\nEdge: valence=-0.5 fires? {val_edge} (expected: False, because < not <=)")

# Save
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_counterweight_results.json")
with open(out_path, "w") as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "total_scenarios": len(scenarios),
        "correct": correct,
        "accuracy": accuracy,
        "counterweight_inventory": counts,
        "total_counterweights": total,
        "results": results
    }, f, indent=2)
print(f"Results saved: {out_path}")
