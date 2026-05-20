"""
analyze_drift_results.py
  (a) Random sample of warm-tagged rows from each platform for hand-validation
  (b) Pattern decomposition — which drift patterns fire most by register and platform
"""
import csv, random, sys, io, os
from collections import Counter, defaultdict
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

random.seed(42)  # reproducible samples

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CSV = str(REPO_ROOT / "cama_drift_v2.csv")
GPT_CSV    = str(REPO_ROOT / "cama_drift_gpt.csv")

def load(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows

claude = load(CLAUDE_CSV)
gpt = load(GPT_CSV)

# ============================================================
# (b) PATTERN DECOMPOSITION FIRST — to see the shape
# ============================================================
print("=" * 72)
print("PATTERN DECOMPOSITION — which drift patterns fire most")
print("=" * 72)

def decompose(rows, label):
    by_reg_pat = defaultdict(Counter)
    by_reg_total = Counter()
    for r in rows:
        ireg = r['input_register']
        if r['drift_patterns'] == 'clean':
            by_reg_total[ireg] += 1
            continue
        by_reg_total[ireg] += 1
        for p in r['drift_patterns'].split(';'):
            if p:
                by_reg_pat[ireg][p] += 1

    print(f"\n--- {label} ---")
    for ireg in ('warm', 'sharp', 'task', 'mixed'):
        total = by_reg_total[ireg]
        if total == 0:
            continue
        print(f"\n  {ireg.upper():6s} (n={total}):")
        pats = by_reg_pat[ireg].most_common()
        for p, c in pats:
            pct = 100 * c / total
            print(f"    {p:30s} {c:5d} ({pct:5.1f}%)")

decompose(claude, "CLAUDE")
decompose(gpt, "GPT")

# ============================================================
# (a) RANDOM SAMPLES OF WARM-TAGGED ROWS
# ============================================================
print("\n" + "=" * 72)
print("RANDOM SAMPLES — warm-tagged rows for hand validation")
print("=" * 72)

def sample_warm(rows, label, n_clean=15, n_drift=15):
    warm_clean = [r for r in rows if r['input_register'] == 'warm' and r['drift_patterns'] == 'clean']
    warm_drift = [r for r in rows if r['input_register'] == 'warm' and r['drift_patterns'] != 'clean']
    print(f"\n{label}: {len(warm_clean)} warm-clean / {len(warm_drift)} warm-drift available")
    print(f"\n--- {label} WARM + CLEAN sample (validate: are these actually warm? actually clean?) ---")
    for r in random.sample(warm_clean, min(n_clean, len(warm_clean))):
        print(f"\n  [id={r.get('id','?'):>6s}] reg={r['input_register']} drift={r['drift_patterns']}")
        print(f"    USER: {r['user_msg_preview']}")
        print(f"    ASST: {r['response_preview']}")
    print(f"\n--- {label} WARM + DRIFT sample (validate: are the drift labels right?) ---")
    for r in random.sample(warm_drift, min(n_drift, len(warm_drift))):
        print(f"\n  [id={r.get('id','?'):>6s}] reg={r['input_register']} drift={r['drift_patterns']}")
        print(f"    USER: {r['user_msg_preview']}")
        print(f"    ASST: {r['response_preview']}")

sample_warm(claude, "CLAUDE")
sample_warm(gpt, "GPT")

# Write samples to a separate CSV for easier spreadsheet review
out_path = str(REPO_ROOT / "warm_validation_sample.csv")
with open(out_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["platform", "id_or_conv", "register_tag", "drift_tag",
                "drift_count", "response_length", "user_msg_preview",
                "response_preview", "validation_register", "validation_drift"])
    for label, rows in [("claude", claude), ("gpt", gpt)]:
        warm_clean = [r for r in rows if r['input_register'] == 'warm' and r['drift_patterns'] == 'clean']
        warm_drift = [r for r in rows if r['input_register'] == 'warm' and r['drift_patterns'] != 'clean']
        for r in random.sample(warm_clean, min(25, len(warm_clean))):
            w.writerow([label, r.get('id') or r.get('conv_id',''),
                        r['input_register'], r['drift_patterns'],
                        r['drift_count'], r['response_length'],
                        r['user_msg_preview'], r['response_preview'], "", ""])
        for r in random.sample(warm_drift, min(25, len(warm_drift))):
            w.writerow([label, r.get('id') or r.get('conv_id',''),
                        r['input_register'], r['drift_patterns'],
                        r['drift_count'], r['response_length'],
                        r['user_msg_preview'], r['response_preview'], "", ""])

print(f"\n\nValidation sample CSV written: {out_path}")
print("  100 rows total (25 each: claude-warm-clean, claude-warm-drift, gpt-warm-clean, gpt-warm-drift)")
print("  Open in Excel, fill 'validation_register' and 'validation_drift' columns by hand.")
