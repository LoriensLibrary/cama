"""Regressions for lost boot context and searches returning their own telemetry."""

import asyncio
import importlib
import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from cama.core import boot_file
from mcp_sections import continuity, retrieval, safety


@pytest.fixture
def store(fresh_db, tmp_path, monkeypatch):
    conn, mcp = fresh_db
    monkeypatch.setenv("CAMA_BOOT_SUMMARY", str(tmp_path / "boot.json"))
    monkeypatch.setattr(retrieval, "_buf_track", lambda *a: None)
    monkeypatch.setattr(retrieval, "_buf_flush_if_ready", lambda: None)
    from cama.temporal import cama_temporal
    monkeypatch.setattr(cama_temporal, "readout", lambda: None)
    return conn, mcp


def seed(store, text, *, context=None, core=0, status="durable", source="teaching", kind="experience"):
    conn, mcp = store
    return conn.execute(
        "INSERT INTO memories(raw_text,memory_type,source_type,status,proposed_by,context,is_core,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (text, kind, source, status, "user" if source == "teaching" else "lorien", context, core, mcp._now(), mcp._now())
    ).lastrowid


def search(store, query, **kwargs):
    store[0].commit()
    return json.loads(asyncio.run(retrieval.cama_search(query, **kwargs)))


def test_search_excludes_self_recorded_queries_and_prefers_phrase(store):
    seed(store, "Session search: Harbor Project", context="auto-recorded", core=1)
    seed(store, "The project has a new harbor", core=1)
    original = seed(store, "Our Harbor Project community")
    result = search(store, "Harbor Project")
    assert result["results"][0]["id"] == original
    assert len(result["results"]) == 2
    assert result["results"][0]["match_kind"] == "phrase"
    assert result["results"][1]["match_kind"] == "all_terms"


@pytest.mark.parametrize("query,decoy", [("CODEX_PROFILE", "CODEXxPROFILE"), ("90%", "9000"), ("a!b", "axb")])
def test_search_treats_wildcards_as_literal_characters(store, query, decoy):
    good = seed(store, query)
    seed(store, decoy, core=1)
    assert [r["id"] for r in search(store, query)["results"]] == [good]


def test_search_partial_match_is_labeled_and_repeated_words_do_not_bias_ranking(store):
    partial = seed(store, "community only", core=1)
    complete = seed(store, "Eden community")
    result = search(store, "community community Eden")
    assert [r["id"] for r in result["results"]] == [complete, partial]
    assert result["results"][0]["query_terms"] == 2
    assert result["results"][1]["match_kind"] == "partial"


def test_search_status_and_query_bounds(store):
    durable = seed(store, "library")
    provisional = seed(store, "library", status="provisional")
    seed(store, "library", status="expired")
    seed(store, "library", status="rejected")
    assert [r["id"] for r in search(store, "library")["results"]] == [durable]
    assert {r["id"] for r in search(store, "library", include_provisional=True)["results"]} == {durable, provisional}
    assert search(store, "  ")["count"] == 0
    assert search(store, "library", limit=-1)["count"] == 0
    assert "error" in search(store, "x" * 2001)
    assert "error" in search(store, " ".join(str(i) for i in range(33)))


@pytest.mark.parametrize("stamp", ["2026-09-25T12:00:00", "2026-09-25T12:00:00Z", "2026-09-25T08:00:00-04:00"])
def test_boot_age_understands_utc_and_local_timestamps(stamp):
    assert boot_file.freshness({"generated_at": stamp}, datetime(2026, 9, 25, 12, 10, tzinfo=timezone.utc)) == {
        "status": "fresh", "age_min": 10.0}


def test_atomic_write_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "boot.json"
    boot_file.write_boot({"old": True}, path)
    def fail(*args):
        raise OSError("disk replacement failed")
    monkeypatch.setattr(boot_file.os, "replace", fail)
    with pytest.raises(OSError):
        boot_file.write_boot({"new": True}, path)
    assert json.loads(path.read_text()) == {"old": True}
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("module", ["cama_sleep", "cama_sleep_v2"])
def test_sleep_refresh_writes_the_file_sessions_actually_read(store, module):
    conn, _ = store
    # Malformed legacy journal context must not prevent all boot refreshes.
    journal = seed(store, "Journal observation", kind="journal", source="inference", context="legacy plain text")
    real = seed(store, "The user's current direction")
    seed(store, "Tool telemetry", context="auto-recorded")
    seed(store, "expired journal", kind="journal", status="expired")
    conn.execute("INSERT INTO aelen_state VALUES ('boot_summary', ?, ?)", ('{"old_snapshot":true}', "2025-01-01"))
    conn.commit()
    sleep = importlib.import_module(f"cama.sleep.{module}")
    result = sleep.refresh_boot_summary(conn)
    assert result["boot_refreshed"] is True
    boot = json.loads(open(result["path"], encoding="utf-8").read())
    assert boot_file.freshness(boot)["status"] == "fresh"
    assert "boot_summary" not in boot["aelen_state"]
    assert {r["id"] for r in boot["recent_topics"]} == {real, journal}
    assert boot["journals"][0]["assistant_identity"] == "lorien"
    assert boot["journals"][0]["source_type"] == "inference"
    resumed = continuity._read_boot_context(conn)
    assert resumed["status"] == "fresh"
    assert resumed["recent_topics"] == boot["recent_topics"]
    assert all(r["created_at"] and r["source_type"] for r in resumed["recent_topics"])


def test_installed_sleep_refresh_does_not_depend_on_repo_working_directory(store, tmp_path):
    import os
    from pathlib import Path
    conn, mcp = store
    seed(store, "a remembered direction")
    conn.commit()
    # Reproduce Task Scheduler's non-repository cwd. Package imports may work
    # while the repository-root cama_mcp.py is deliberately not importable.
    package = Path(__file__).resolve().parents[1] / "cama"
    code = '''
import importlib.abc, sys, types
from pathlib import Path
class NoEntrypoint(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'cama_mcp':
            raise ModuleNotFoundError("entrypoint unavailable outside repository")
sys.meta_path.insert(0, NoEntrypoint())
pkg = types.ModuleType('cama')
pkg.__path__ = [sys.argv[1]]
sys.modules['cama'] = pkg
from cama.sleep import cama_sleep
c = cama_sleep.get_db()
result = cama_sleep.refresh_boot_summary(c)
assert result['boot_refreshed']
assert Path(result['path']).exists()
c.close()
'''
    env = dict(os.environ, CAMA_DB_PATH=mcp.DB_PATH, CAMA_BOOT_SUMMARY=str(tmp_path / "scheduled.json"))
    result = subprocess.run([sys.executable, "-c", code, str(package)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("existing", [None, "{broken", '{"generated_at":"invalid"}', "[]"])
def test_session_repairs_missing_or_unreadable_boot(store, existing):
    if existing is not None:
        from pathlib import Path
        Path(boot_file.boot_path()).write_text(existing, encoding="utf-8")
    result = continuity._read_boot_context(store[0])
    assert result["status"] == "refreshed"
    assert result["age_min"] is not None and result["age_min"] < 1


def test_failed_session_refresh_never_reports_refreshed(store, monkeypatch):
    boot_file.write_boot({"generated_at": "2020-01-01T00:00:00Z"})
    monkeypatch.setattr(continuity, "_refresh_boot_summary", lambda c: None)
    result = continuity._read_boot_context(store[0])
    assert result["status"] == "cold"
    assert "refresh_error" in result


def prepare_health(store):
    conn, mcp = store
    from cama.sleep import cama_sleep
    old = cama_sleep.DB_PATH
    cama_sleep.DB_PATH = mcp.DB_PATH
    try:
        cama_sleep.get_db().close()
    finally:
        cama_sleep.DB_PATH = old
    conn.execute("CREATE TABLE IF NOT EXISTS librarian_membership (memory_id INTEGER, librarian_id INTEGER)")
    conn.execute("INSERT INTO daily_context(date,last_updated) VALUES (?,?)", (mcp._now()[:10], mcp._now()))
    conn.commit()


def test_health_distinguishes_pending_from_processed_unclaimed(store):
    conn, mcp = store
    prepare_health(store)
    seed(store, "not processed")
    unclaimed = seed(store, "no topic matched")
    conn.execute("UPDATE memories SET pattern_source='unclaimed' WHERE id=?", (unclaimed,))
    conn.commit()
    boot_file.write_boot({"generated_at": mcp._now()})
    result = json.loads(asyncio.run(safety.cama_health()))
    assert result["librarian_unassigned"] == 2
    assert result["librarian_pending"] == 1
    assert result["librarian_unclaimed"] == 1
    assert "1 durable memories await" in " ".join(result["warnings"])


def test_corrupt_boot_file_does_not_claim_database_is_down(store):
    from pathlib import Path
    prepare_health(store)
    Path(boot_file.boot_path()).write_text("{broken", encoding="utf-8")
    result = json.loads(asyncio.run(safety.cama_health()))
    assert result["db_reachable"] is True
    assert result["boot_status"] == "error"
    assert result["confidence_level"] == "degraded"


@pytest.mark.parametrize("module", ["cama_sleep", "cama_sleep_v2", "cama_loop"])
def test_daily_refresh_supports_new_schema_and_preserves_existing_context(store, module):
    conn, mcp = store
    seed(store, "a current conversation")
    from cama.core.daily_context import upsert_daily_context
    upsert_daily_context(conn, mcp._now()[:10], {
        "thread_count": 4, "thread_summaries": ["keep this"],
        "emotional_arc": [{"hour": 8, "valence_mean": 0.1}], "dominant_types": {"experience": 1}})
    conn.commit()
    sleep = importlib.import_module(f"cama.sleep.{module}")
    assert sleep.refresh_daily_context(conn)["days_refreshed"] == 1
    row = conn.execute("SELECT * FROM daily_context WHERE date=?", (mcp._now()[:10],)).fetchone()
    assert row["thread_count"] == 4
    assert json.loads(row["thread_summaries"]) == ["keep this"]
    assert json.loads(row["emotional_arc"]) == [{"hour": 8, "valence_mean": 0.1}]
    assert row["memory_count"] == 1
    # The MCP writer must likewise keep the daemon's aggregate fields.
    mcp._build_daily_context(conn)
    assert json.loads(conn.execute("SELECT thread_summaries FROM daily_context").fetchone()[0]) == ["keep this"]


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_sleep_refresh_runs_after_ingestion_and_logs_failures(store, monkeypatch, refresh_fails):
    from cama.librarian import cama_auto_tag
    from cama.sleep import cama_sleep as sleep
    conn, mcp = store
    monkeypatch.setattr(sleep, "DB_PATH", mcp.DB_PATH)
    monkeypatch.setattr(sleep, "consolidate_memories", lambda c: dict(clusters_found=0, edges_created=0,
                        cross_temporal_hits=0, comparisons=0, duplicates_expired=0))
    monkeypatch.setattr(sleep, "write_dream_entry", lambda c: None)
    monkeypatch.setattr(sleep, "decay_provisionals", lambda c: {"expired": 0})
    monkeypatch.setattr(sleep, "backfill_embeddings", lambda c: {"backfilled": 0})
    monkeypatch.setattr(cama_auto_tag, "backfill", lambda **kw: {})
    order = []
    def ingest(c):
        seed((c, mcp), "new turn imported during cycle")
        c.commit()
        order.append("ingest")
        return {"written": 1}
    def refresh_daily(c):
        order.append("refresh")
        return {"days_refreshed": 0}
    monkeypatch.setattr(sleep, "ingest_new_threads", ingest)
    monkeypatch.setattr(sleep, "refresh_daily_context", refresh_daily)
    if refresh_fails:
        def fail(c):
            raise OSError("boot disk unavailable")
        monkeypatch.setattr(sleep, "refresh_boot_summary", fail)
    actions = sleep.run_sleep_cycle()
    assert order == ["ingest", "refresh"]
    assert actions["status"] == ("degraded" if refresh_fails else "ok")
    logged = json.loads(conn.execute("SELECT actions_taken FROM sleep_log ORDER BY id DESC LIMIT 1").fetchone()[0])
    if refresh_fails:
        assert logged["refresh"]["boot_refreshed"] is False
        assert "boot disk unavailable" in logged["refresh"]["error"]
    else:
        assert continuity._read_boot_context(conn)["recent_topics"][0]["text"] == "new turn imported during cycle"


@pytest.mark.parametrize("has_memory", [False, True])
def test_missing_daily_context_only_warns_when_durable_memories_exist(store, has_memory):
    conn, mcp = store
    prepare_health(store)
    conn.execute("DELETE FROM daily_context")
    if has_memory:
        mid = seed(store, "today's conversation")
        conn.execute("UPDATE memories SET pattern_source='unclaimed' WHERE id=?", (mid,))
    conn.commit()
    boot_file.write_boot({"generated_at": mcp._now()})
    result = json.loads(asyncio.run(safety.cama_health()))
    assert result["daily_context_today"] is False
    assert result["daily_context_required"] is has_memory
    assert result["durable_memories_today"] == int(has_memory)
    assert any("daily_context" in warning for warning in result["warnings"]) is has_memory
    assert result["confidence_level"] == ("degraded" if has_memory else "nominal")
