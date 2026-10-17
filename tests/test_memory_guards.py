import asyncio
import json
import sqlite3

import pytest

from cama.core.memory_guards import install
from mcp_sections import memory_lifecycle as lifecycle


@pytest.fixture
def guarded(fresh_db, monkeypatch):
    c, m = fresh_db
    install(c)
    monkeypatch.setattr(m, "EMBEDDING_PROVIDER", "none")
    c.executescript('''
      CREATE TABLE librarians(id INTEGER PRIMARY KEY, name TEXT, category TEXT,
        routing_keywords TEXT, member_count INTEGER DEFAULT 0, updated_at TEXT);
      CREATE TABLE librarian_membership(librarian_id INTEGER, memory_id INTEGER,
        membership_strength REAL, assigned_by TEXT, assigned_at TEXT,
        UNIQUE(librarian_id,memory_id));
      INSERT INTO librarians VALUES(1,'test','topic','["observation"]',0,NULL);
    ''')
    return c, m


def store(m, kind="inference"):
    if kind == "teaching":
        return lifecycle.cama_store_teaching(m.StoreTeachingInput(raw_text="observation"))
    if kind == "exchange":
        return lifecycle.cama_store_exchange(m.StoreExchangeInput(user_message="observation", assistant_message="response"))
    return lifecycle.cama_store_inference(m.StoreInferenceInput(
        raw_text="observation", memory_type="self_account", consent_level="medium",
        context=json.dumps({"author":"aelen", "subject":"aelen"})))


def saved(m, kind="inference"):
    return json.loads(asyncio.run(store(m, kind)))["memory_id"]


@pytest.mark.parametrize("sql", [
    "UPDATE memories SET raw_text='changed' WHERE id=?",
    "UPDATE memories SET memory_type='pattern' WHERE id=?",
    "UPDATE memories SET source_type='teaching' WHERE id=?",
    "UPDATE memories SET proposed_by='user' WHERE id=?",
    "UPDATE memories SET id=id+100 WHERE id=?",
    "UPDATE memories SET context='{\"author\":\"lorien\",\"subject\":\"lorien\"}' WHERE id=?",
    "UPDATE memories SET context='broken' WHERE id=?",
    "INSERT OR REPLACE INTO memories(id,raw_text,created_at,updated_at) VALUES(?,'replacement','now','now')",
])
def test_self_account_bypasses_blocked(guarded, sql):
    c,m = guarded
    mid = saved(m)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(sql, (mid,))
    c.rollback()
    assert c.execute("SELECT raw_text FROM memories WHERE id=?", (mid,)).fetchone()[0] == "observation"


@pytest.mark.parametrize("assignment", ["source_type='inference'", "proposed_by='assistant'"])
def test_teaching_provenance(guarded, assignment):
    c,m = guarded
    mid = saved(m, "teaching")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE memories SET " + assignment + " WHERE id=?", (mid,))
    c.rollback()


def test_real_lifecycle_audits_context_and_delete(guarded):
    c,m = guarded
    mid = saved(m)
    asyncio.run(lifecycle.cama_confirm_memory(mid))
    assert json.loads(c.execute("SELECT context FROM memories WHERE id=?", (mid,)).fetchone()[0])["status"] == "durable"
    asyncio.run(lifecycle.cama_reject_memory(mid, "revised"))
    assert c.execute("SELECT count(*) FROM memory_log WHERE memory_id=? AND field='status'", (mid,)).fetchone()[0] == 2
    c.execute("DELETE FROM memories WHERE id=?", (mid,))
    c.commit()
    audit = c.execute("SELECT old_value,new_value,note FROM memory_log WHERE field='deleted'").fetchone()
    assert audit[0] == "self_account" and "observation" not in str(tuple(audit))


def test_audit_rolls_back_and_noops_do_not_log(guarded):
    c,m = guarded
    mid = saved(m)
    n = c.execute("SELECT count(*) FROM memory_log").fetchone()[0]
    c.execute("UPDATE memories SET status=status WHERE id=?", (mid,))
    c.commit()
    assert c.execute("SELECT count(*) FROM memory_log").fetchone()[0] == n
    c.execute("UPDATE memories SET consent_level='high' WHERE id=?", (mid,))
    c.rollback()
    assert c.execute("SELECT count(*) FROM memory_log").fetchone()[0] == n
    install(c)
    assert c.execute("SELECT count(*) FROM memory_log").fetchone()[0] == n


@pytest.mark.parametrize("kind", ["teaching", "inference", "exchange"])
@pytest.mark.parametrize("failure", ["affect", "membership", "cache"])
def test_actual_store_rolls_back_all_tables(guarded, kind, failure):
    c,m = guarded
    table = {"affect":"memory_affect", "membership":"librarian_membership", "cache":"librarians"}[failure]
    event = "UPDATE" if failure == "cache" else "INSERT"
    c.executescript(f"CREATE TRIGGER inject_failure BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'injected failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        asyncio.run(store(m, kind))
    for table in ("memories", "memory_affect", "librarian_membership", "memory_log", "ring"):
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["teaching", "inference", "exchange"])
def test_ring_failure_preserves_complete_shelf(guarded, monkeypatch, kind):
    c,m = guarded
    def fail(*args):
        raise RuntimeError("ring unavailable")
    monkeypatch.setattr(lifecycle, "_ring_push", fail)
    mid = saved(m, kind)
    for table in ("memory_affect", "librarian_membership"):
        assert c.execute(f"SELECT count(*) FROM {table} WHERE memory_id=?", (mid,)).fetchone()[0] == 1


def test_migration_failure_is_atomic(fresh_db):
    c,m = fresh_db
    # A conflicting object causes installation to fail after BEGIN.
    c.execute("CREATE VIEW memory_log AS SELECT id FROM memories")
    c.commit()
    with pytest.raises(sqlite3.OperationalError):
        install(c)
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='cama_guard_migrations'").fetchone()


def test_secondary_unique_index_requires_review(fresh_db):
    c,m = fresh_db
    c.execute("CREATE UNIQUE INDEX extra_unique ON memories(source_msg_id)")
    c.commit()
    with pytest.raises(RuntimeError, match="Secondary UNIQUE"):
        install(c)
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='cama_guard_migrations'").fetchone()

