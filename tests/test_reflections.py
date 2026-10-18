import asyncio
import json
import sqlite3

import pytest
from mcp.server.fastmcp import FastMCP

from cama.self_model import cama_thinking_log as log
from cama.self_model import reflections as r


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(log, "DB_PATH", str(tmp_path / "log.db"))
    r.init_schema()
    c = log._get_db()
    yield c
    c.close()


def save(author="lorien", **kwargs):
    return r.store(r.ReflectionInput(author=author, account="I revised my conclusion.",
                                    rationale="The test supplied contradictory evidence.", **kwargs))


def test_authorship_privacy_and_legacy_isolation(db):
    own = save()["id"]
    save("aelen")
    private = save(consent_level="high")["id"]
    assert [x["id"] for x in r.read(db, "lorien")["records"]] == [own]
    assert [x["id"] for x in r.read(db, "lorien", include_sensitive=True)["records"]] == [private, own]
    assert r.read(db, "unknown")["records"] == []
    assert json.loads(log._think_query_impl())["entries"] == []
    assert "memories" not in {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_revisions_preserve_history_and_cannot_cross_authors(db):
    original = save()["id"]
    revised = save(revises_id=original, changed_mind="I found counterevidence.")["id"]
    rows = r.read(db, "lorien")["records"]
    assert [x["id"] for x in rows] == [revised, original]
    assert rows[0]["revises_id"] == original
    assert rows[1]["changed_mind"] == ""
    with pytest.raises(ValueError):
        save("aelen", revises_id=original)
    result = json.loads(log._think_review_impl(log.ThinkReviewInput(log_id=original, review="overwrite")))
    assert "error" in result
    assert db.execute("SELECT later_review FROM thinking_log WHERE id=?", (original,)).fetchone()[0] is None


def test_revision_cannot_downgrade_sensitivity(db):
    private = save(consent_level="high")["id"]
    with pytest.raises(ValueError):
        save(revises_id=private)
    assert save(revises_id=private, consent_level="high")["stored"]


def test_quarantine_cannot_enter_recall(db, monkeypatch):
    monkeypatch.setattr(r.cama_trust, "classify", lambda *args: {"quarantined": True})
    assert save()["status"] == "quarantined"
    assert r.read(db, "lorien", include_sensitive=True)["records"] == []


def test_pagination_startup_and_no_migration_on_read(db):
    ids = [save()["id"] for _ in range(5)]
    first = r.read(db, "lorien", limit=2)
    second = r.read(db, "lorien", limit=2, before_id=first["next_before_id"])
    assert [x["id"] for x in first["records"] + second["records"]] == ids[:0:-1]
    assert len(r.read(db, "lorien", limit=3, startup=True)["records"]) == 3
    old = sqlite3.connect(":memory:")
    assert r.read(old, "lorien")["available"] is False
    assert old.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    old.close()


def test_legacy_rows_remain_unattributed(db):
    db.execute("INSERT INTO thinking_log(what_im_noticing,created_at) VALUES('legacy','2026-01-01')")
    db.commit()
    r.init_schema()
    assert db.execute("SELECT author FROM thinking_log").fetchone()[0] is None
    assert r.read(db, "lorien")["records"] == []
    assert len(json.loads(log._think_query_impl())["entries"]) == 1


def test_registration_and_tool_roundtrip(db):
    mcp = FastMCP("reflection-test")
    log.register(mcp)
    result = json.loads(asyncio.run(mcp._tool_manager.call_tool("cama_reflect", {"params": {
        "author": "lorien", "account": "I corrected the implementation.", "rationale": "A test failed."}})))
    recalled = json.loads(asyncio.run(mcp._tool_manager.call_tool("cama_reflections", {"author": "lorien"})))
    assert recalled["records"][0]["id"] == result["id"]
