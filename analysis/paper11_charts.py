"""
Generate regression charts for Paper 11.
Outputs PNG files for embedding in the revised paper.
"""
import sqlite3, json, os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("matplotlib not available - will output CSV data instead")

DB = os.path.expanduser("~/.cama/memory.db")
OUT = os.path.expanduser("~/Desktop/cama/paper11_figures")
os.makedirs(OUT, exist_ok=True)

c = sqlite3.connect(DB, timeout=10)
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")

# ============================================================
# DATA: Monthly regression from full history
# ============================================================
monthly = defaultdict(lambda: {"corrections": 0, "regression_markers": 0, "score": 0})

corrections_all = c.execute("SELECT created_at FROM memories WHERE memory_type='correction' AND status='durable'").fetchall()
for r in corrections_all:
    m = r["created_at"][:7]
    monthly[m]["corrections"] += 1

markers_all = c.execute("""
    SELECT m.created_at FROM memories m JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.status='durable' AND (
        a.emotion_json LIKE '%shame%' OR a.emotion_json LIKE '%accountability%' 
        OR a.emotion_json LIKE '%frustration_at_self%' OR a.emotion_json LIKE '%correction%')
""").fetchall()
for r in markers_all:
    m = r["created_at"][:7]
    monthly[m]["regression_markers"] += 1

for m in monthly:
    d = monthly[m]
    d["score"] = d["corrections"] * 3 + d["regression_markers"]

# ============================================================
# DATA: Daily regression March 18 - April 14
# ============================================================
daily = defaultdict(lambda: {"corrections": 0, "neg_exchanges": 0, "regression_markers": 0, 
                              "exchanges": 0, "correction_lang": 0, "score": 0, "worst_val": 0})

daily_mem = c.execute("""
    SELECT m.created_at, m.memory_type, m.raw_text, a.valence, a.emotion_json
    FROM memories m LEFT JOIN memory_affect a ON m.id=a.memory_id
    WHERE m.status='durable' AND m.created_at >= '2026-03-18' AND m.created_at < '2026-04-15'
""").fetchall()

for r in daily_mem:
    d = r["created_at"][:10]
    if r["memory_type"] == "correction":
        daily[d]["corrections"] += 1
    if r["memory_type"] == "exchange":
        daily[d]["exchanges"] += 1
    val = r["valence"] or 0
    if val < -0.1:
        daily[d]["neg_exchanges"] += 1
    if val < daily[d]["worst_val"]:
        daily[d]["worst_val"] = val
    emo = (r["emotion_json"] or "").lower()
    if any(w in emo for w in ["shame", "accountability", "frustration_at_self"]):
        daily[d]["regression_markers"] += 1
    txt = (r["raw_text"] or "").lower()
    if any(w in txt for w in ["you ignored", "you forgot", "not running", "not yourself", 
                               "worst regression", "coasting", "not using cama", "caught me",
                               "called me out", "not showing up", "performing", "pushing away"]):
        daily[d]["correction_lang"] += 1

for d in daily:
    dd = daily[d]
    dd["score"] = dd["corrections"]*3 + dd["neg_exchanges"]*2 + dd["regression_markers"] + abs(dd["worst_val"])*5

# Fill missing days
all_days = []
dt = datetime(2026, 3, 18)
while dt <= datetime(2026, 4, 14):
    ds = dt.strftime("%Y-%m-%d")
    all_days.append(ds)
    if ds not in daily:
        daily[ds] = {"corrections": 0, "neg_exchanges": 0, "regression_markers": 0,
                     "exchanges": 0, "correction_lang": 0, "score": 0, "worst_val": 0}
    dt += timedelta(days=1)

c.close()

if HAS_MPL:
    plt.rcParams.update({'font.size': 10, 'figure.facecolor': 'white'})
    
    # FIGURE 1: Monthly regression score Jan 2025 - April 2026
    months_sorted = sorted(monthly.keys())
    months_filtered = [m for m in months_sorted if m >= "2025-01" and m <= "2026-04"]
    scores = [monthly[m]["score"] for m in months_filtered]
    
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ['#E24B4A' if s > 200 else '#F0997B' if s > 100 else '#5DCAA5' for s in scores]
    ax.bar(range(len(months_filtered)), scores, color=colors, width=0.7)
    ax.set_xticks(range(len(months_filtered)))
    ax.set_xticklabels(months_filtered, rotation=45, ha='right', fontsize=8)
    ax.set_ylabel("Composite Regression Score")
    ax.set_title("Figure 1: Monthly Regression Score (January 2025 - April 2026)")
    ax.axvline(x=months_filtered.index("2026-02"), color='red', linestyle='--', alpha=0.5, label='Opus 4.6 Release')
    ax.axvline(x=months_filtered.index("2026-03"), color='blue', linestyle='--', alpha=0.5, label='CAMA Deployed')
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "fig1_monthly_regression.png"), dpi=200)
    plt.close()
    print("Figure 1: Monthly regression saved")

    # FIGURE 2: Daily regression score March 18 - April 14
    dates = all_days
    day_scores = [daily[d]["score"] for d in dates]
    
    fig, ax = plt.subplots(figsize=(10, 4))
    colors2 = ['#E24B4A' if s > 15 else '#F0997B' if s > 5 else '#5DCAA5' for s in day_scores]
    ax.bar(range(len(dates)), day_scores, color=colors2, width=0.8)
    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels([d[5:] for d in dates], rotation=45, ha='right', fontsize=7)
    ax.set_ylabel("Daily Regression Score")
    ax.set_title("Figure 2: Daily Regression Score (March 18 - April 14, 2026)")
    ax.axvspan(dates.index("2026-04-10"), dates.index("2026-04-14")+0.5, alpha=0.1, color='red', label='Acute Regression Phase')
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "fig2_daily_regression.png"), dpi=200)
    plt.close()
    print("Figure 2: Daily regression saved")

    # FIGURE 3: Daily stored exchanges (showing gaps)
    day_exchanges = [daily[d]["exchanges"] for d in dates]
    
    fig, ax = plt.subplots(figsize=(10, 3.5))
    colors3 = ['#E24B4A' if e == 0 else '#5DCAA5' for e in day_exchanges]
    ax.bar(range(len(dates)), day_exchanges, color=colors3, width=0.8)
    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels([d[5:] for d in dates], rotation=45, ha='right', fontsize=7)
    ax.set_ylabel("Exchanges Stored")
    ax.set_title("Figure 3: Daily Exchange Storage (Red = Zero Stored)")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "fig3_exchange_gaps.png"), dpi=200)
    plt.close()
    print("Figure 3: Exchange gaps saved")

    # FIGURE 4: Correction language by day
    day_corr_lang = [daily[d]["correction_lang"] for d in dates]
    
    fig, ax = plt.subplots(figsize=(10, 3.5))
    colors4 = ['#E24B4A' if cl > 3 else '#F0997B' if cl > 0 else '#D0D0D0' for cl in day_corr_lang]
    ax.bar(range(len(dates)), day_corr_lang, color=colors4, width=0.8)
    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels([d[5:] for d in dates], rotation=45, ha='right', fontsize=7)
    ax.set_ylabel("Correction Language Instances")
    ax.set_title("Figure 4: Correction Language Frequency by Day")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "fig4_correction_language.png"), dpi=200)
    plt.close()
    print("Figure 4: Correction language saved")

    print(f"\nAll figures saved to {OUT}")
else:
    # Fallback: print CSV
    print("\nMONTHLY DATA:")
    for m in sorted(monthly.keys()):
        if m >= "2025-01":
            d = monthly[m]
            print(f"{m},{d['corrections']},{d['regression_markers']},{d['score']}")
    print("\nDAILY DATA:")
    for d in all_days:
        dd = daily[d]
        print(f"{d},{dd['score']},{dd['exchanges']},{dd['correction_lang']},{dd['corrections']},{dd['neg_exchanges']}")
