"""Provisional memories get a place to be seen and decided before they expire."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from cama.core.boot_payload import PRIORITY, budget_boot
from cama.core.pending_confirmations import load_pending_confirmations, pending_summary
from mcp_sections import memory_lifecycle, safety


def run(coro):
    return json.loads(asyncio.run(coro))


def provisional(c, text, hours=None, consent="low", kind="insight"):
    now = datetime.now(timezone.utc)
    review_after = (now + timedelta(hours=hours)).isoformat() if hours is not None else None
    cur = c.execute(
        "INSERT INTO memories (raw_text, memory_type, source_type, status, proposed_by, "
        "needs_user_confirmation, consent_level, created_at, updated_at, review_after) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (text, kind, "inference", "provisional", "assistant", 1, consent,
         now.isoformat(), now.isoformat(), review_after))
    c.commit()
    return cur.lastrowid


def test_loader_orders_soonest_first_caps_and_redacts(fresh_db):
    c, _ = fresh_db
    late = provisional(c, "late", hours=100)
    soon = provisional(c, "soon", hours=1)
    mid = provisional(c, "mid", hours=30)
    orphan = provisional(c, "orphan without review_after", hours=None)
    secret = provisional(c, "SENSITIVE_TEXT", hours=2, consent="high")
    for i in range(3):
        provisional(c, f"filler {i}", hours=200 + i)
    out = load_pending_confirmations(c)
    assert out["total_pending"] == 8 and out["shown"] == 5 and len(out["records"]) == 5
    assert out["expiring_within_48h"] == 3
    ids = [r["id"] for r in out["records"]]
    assert ids[:3] == [soon, secret, mid]
    assert late in ids and orphan not in ids
    redacted = next(r for r in out["records"] if r["id"] == secret)
    assert "SENSITIVE_TEXT" not in redacted["text"] and redacted["consent_level"] == "high"
    assert pending_summary(c) == {"provisional": 8, "expiring_within_48h": 3}
    full = load_pending_confirmations(c, limit=50)
    orphan_row = next(r for r in full["records"] if r["id"] == orphan)
    assert 335 < orphan_row["hours_left"] < 337


def test_boot_budget_keeps_the_section(fresh_db):
    c, _ = fresh_db
    for i in range(6):
        provisional(c, "x" * 500, hours=5 + i)
    assert PRIORITY.index("pending_confirmations") == PRIORITY.index("self_accounts") + 1
    packed = json.loads(budget_boot({"pending_confirmations": load_pending_confirmations(c)}, 4000))
    assert packed["coverage"]["sections"]["pending_confirmations"]["status"] in ("included", "trimmed")
    assert len(packed["pending_confirmations"]["records"]) >= 4
    assert packed["pending_confirmations"]["total_pending"] == 6


def test_review_pending_batch_confirms_rejects_and_reports_errors(fresh_db, monkeypatch):
    c, m = fresh_db
    monkeypatch.setattr(m, "EMBEDDING_PROVIDER", "none")
    keep = provisional(c, "keep me", hours=3)
    drop = provisional(c, "drop me", hours=3)
    stay = provisional(c, "undecided", hours=3)
    durable = provisional(c, "already durable", hours=3)
    c.execute("UPDATE memories SET status='durable' WHERE id=?", (durable,)); c.commit()

    assert "error" in run(memory_lifecycle.cama_review_pending(confirm=[keep], reject=[keep]))

    out = run(memory_lifecycle.cama_review_pending(confirm=[keep, 999999], reject=[drop, durable], reason="not true"))
    assert out["confirmed"] == [keep] and out["rejected"] == [drop]
    assert {e["id"] for e in out["errors"]} == {999999, durable}
    assert out["remaining"] == {"provisional": 1, "expiring_within_48h": 1}

    rows = {r[0]: r for r in c.execute("SELECT id, status, needs_user_confirmation, context FROM memories").fetchall()}
    assert rows[keep][1] == "durable" and rows[keep][2] == 0
    assert rows[drop][1] == "rejected" and "not true" in (rows[drop][3] or "")
    assert rows[stay][1] == "provisional"
    assert rows[durable][1] == "durable"


def test_health_counts_and_warns_about_expiring_provisionals(fresh_db):
    c, _ = fresh_db
    # Health reads the sleep heartbeat and librarian coverage; a fresh store has neither table yet.
    c.execute("CREATE TABLE IF NOT EXISTS sleep_log (id INTEGER PRIMARY KEY, cycle_start TEXT, cycle_end TEXT, "
              "actions_taken TEXT, dream_entry TEXT, memories_consolidated INTEGER, memories_expired INTEGER, "
              "embeddings_backfilled INTEGER, edges_created INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS librarian_membership (librarian_id INTEGER, memory_id INTEGER, "
              "membership_strength REAL, assigned_by TEXT, assigned_at TEXT)")
    c.commit()
    provisional(c, "about to vanish", hours=2)
    provisional(c, "plenty of time", hours=300)
    health = run(safety.cama_health())
    assert health.get("db_reachable") and "error" not in health, health
    assert health["provisional_pending"] == 2
    assert health["provisional_expiring_48h"] == 1
    assert any("cama_review_pending" in w for w in health["warnings"])


def test_boot_summary_carries_the_counts(fresh_db):
    c, _ = fresh_db
    from cama.core.boot_file import boot_path
    from cama.core.boot_summary import refresh_boot_summary
    provisional(c, "soon", hours=1)
    refresh_boot_summary(c)
    with open(boot_path(), encoding="utf-8") as fh:
        boot = json.load(fh)
    assert boot["pending_confirmations"] == {"provisional": 1, "expiring_within_48h": 1}
