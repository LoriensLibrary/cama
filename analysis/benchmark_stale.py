"""
CAMA Layer 2 Benchmark, Stale Memory Detection
Checks for old unconfirmed provisionals, orphaned data, and memory hygiene.
"""
import sqlite3, os, json
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/.cama/memory.db")

c = sqlite3.connect(DB_PATH)
c.row_factory = sqlite3.Row

print(f"CAMA Stale Memory Detection, {datetime.now().isoformat()}")
print(f"Database: {DB_PATH}")
print("=" * 70)

# 1. Provisional count and age
provs = c.execute("SELECT id, raw_text, created_at, review_after, confidence FROM memories WHERE status='provisional' ORDER BY created_at ASC").fetchall()
print(f"\n1. PROVISIONAL MEMORIES: {len(provs)}")
if provs:
    oldest = provs[0]["created_at"]
    newest = provs[-1]["created_at"]
    with_review = sum(1 for p in provs if p["review_after"])
    without_review = len(provs) - with_review
    low_conf = sum(1 for p in provs if p["confidence"] and p["confidence"] < 0.5)
    print(f"   Oldest: {oldest}")
    print(f"   Newest: {newest}")
    print(f"   With review_after set: {with_review}")
    print(f"   Without review_after (never expires): {without_review}")
    print(f"   Low confidence (<0.5): {low_conf}")
    
    # Age buckets
    now = datetime.now()
    buckets = {"<7d": 0, "7-14d": 0, "14-30d": 0, "30-60d": 0, ">60d": 0}
    for p in provs:
        try:
            created = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
            age = (now - created).days
            if age < 7: buckets["<7d"] += 1
            elif age < 14: buckets["7-14d"] += 1
            elif age < 30: buckets["14-30d"] += 1
            elif age < 60: buckets["30-60d"] += 1
            else: buckets[">60d"] += 1
        except:
            buckets[">60d"] += 1
    print(f"   Age distribution: {json.dumps(buckets)}")

# 2. Status distribution
print(f"\n2. STATUS DISTRIBUTION:")
statuses = c.execute("SELECT status, COUNT(*) as cnt FROM memories GROUP BY status ORDER BY cnt DESC").fetchall()
for s in statuses:
    print(f"   {s['status']:15s}: {s['cnt']:6d}")

# 3. Expired count
expired = c.execute("SELECT COUNT(*) FROM memories WHERE status='expired'").fetchone()[0]
rejected = c.execute("SELECT COUNT(*) FROM memories WHERE status='rejected'").fetchone()[0]
print(f"\n3. CLEANUP STATUS:")
print(f"   Expired: {expired}")
print(f"   Rejected: {rejected}")

# 4. Memories with no affect data
no_affect = c.execute("SELECT COUNT(*) FROM memories m LEFT JOIN memory_affect a ON m.id=a.memory_id WHERE a.id IS NULL AND m.status='durable'").fetchone()[0]
print(f"\n4. DURABLE MEMORIES MISSING AFFECT: {no_affect}")

# 5. Memories with no embeddings
no_embed = c.execute("SELECT COUNT(*) FROM memories m LEFT JOIN memory_embeddings e ON m.id=e.memory_id WHERE e.memory_id IS NULL AND m.status='durable'").fetchone()[0]
total_durable = c.execute("SELECT COUNT(*) FROM memories WHERE status='durable'").fetchone()[0]
print(f"\n5. EMBEDDING COVERAGE:")
print(f"   Durable memories: {total_durable}")
print(f"   Missing embeddings: {no_embed}")
print(f"   Coverage: {((total_durable - no_embed) / total_durable * 100) if total_durable else 0:.1f}%")

# 6. Ring health
ring_count = c.execute("SELECT COUNT(*) FROM ring").fetchone()[0]
ring_cap = 30
orphan_ring = c.execute("SELECT COUNT(*) FROM ring r LEFT JOIN memories m ON r.memory_id=m.id WHERE m.id IS NULL").fetchone()[0]
print(f"\n6. RING HEALTH:")
print(f"   Occupied: {ring_count}/{ring_cap}")
print(f"   Orphaned slots (memory deleted): {orphan_ring}")

# 7. Duplicate detection (same raw_text)
dupes = c.execute("SELECT raw_text, COUNT(*) as cnt FROM memories WHERE status='durable' GROUP BY raw_text HAVING cnt > 1 ORDER BY cnt DESC LIMIT 5").fetchall()
total_dupes = c.execute("SELECT SUM(cnt) FROM (SELECT COUNT(*) as cnt FROM memories WHERE status='durable' GROUP BY raw_text HAVING cnt > 1)").fetchone()[0]
print(f"\n7. DUPLICATE DETECTION:")
print(f"   Total duplicate groups: {len(dupes)} (showing top 5)")
print(f"   Total duplicate memories: {total_dupes or 0}")
for d in dupes:
    print(f"   [{d['cnt']}x] {d['raw_text'][:80]}...")

print("\n" + "=" * 70)

# Summary verdict
issues = []
if len(provs) > 50:
    issues.append(f"{len(provs)} unconfirmed provisionals accumulating")
if no_affect > 1000:
    issues.append(f"{no_affect} durable memories missing affect data")
if no_embed > 1000:
    issues.append(f"{no_embed} durable memories missing embeddings")
if orphan_ring > 0:
    issues.append(f"{orphan_ring} orphaned ring slots")
if total_dupes and total_dupes > 100:
    issues.append(f"{total_dupes} duplicate memories")

if issues:
    print("ISSUES FOUND:")
    for issue in issues:
        print(f"  - {issue}")
else:
    print("ALL CLEAN")

# Save
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_stale_results.json")
with open(out_path, "w") as f:
    json.dump({
        "timestamp": datetime.now().isoformat(),
        "provisionals": len(provs),
        "status_distribution": {s["status"]: s["cnt"] for s in statuses},
        "expired": expired,
        "rejected": rejected,
        "missing_affect": no_affect,
        "missing_embeddings": no_embed,
        "total_durable": total_durable,
        "embedding_coverage_pct": round((total_durable - no_embed) / total_durable * 100, 1) if total_durable else 0,
        "ring_occupied": ring_count,
        "ring_orphaned": orphan_ring,
        "duplicate_count": total_dupes or 0,
        "issues": issues
    }, f, indent=2)
print(f"\nResults saved: {out_path}")
c.close()
