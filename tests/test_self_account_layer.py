import asyncio
import json

import pytest

from cama.core.boot_payload import budget_boot
from cama.core.self_accounts import load_self_accounts, matches_leaf
from mcp_sections import memory_lifecycle, retrieval


def run(coro):
    return json.loads(asyncio.run(coro))


def store(m, author="aelen", **kw):
    return run(memory_lifecycle.cama_store_inference(m.StoreInferenceInput(
        raw_text="SELF_ACCOUNT observation", memory_type="self_account",
        context=json.dumps({"author": author, "subject": author, "status": "provisional", "lesson": "Ask before assigning an ambiguous reference."}), **kw)))


def test_review_activation_and_lifecycle(fresh_db, monkeypatch):
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    result=store(m)
    mid=result["memory_id"]
    assert not result["ring_ok"]
    assert not c.execute("SELECT 1 FROM ring WHERE memory_id=?",(mid,)).fetchone()
    m._ring_push(c,mid,"explicit review")
    assert not c.execute("SELECT 1 FROM ring WHERE memory_id=?",(mid,)).fetchone()
    assert load_self_accounts(c,"aelen")["records"] == []
    out=run(retrieval.cama_query_memories(m.QueryInput(query_text="SELF_ACCOUNT observation")))
    assert str(mid) not in [str(x["id"]) for x in out.get("results",out.get("memories",[]))]
    run(memory_lifecycle.cama_confirm_memory(mid))
    row=c.execute("SELECT * FROM memories WHERE id=?",(mid,)).fetchone()
    assert row["source_type"] == "inference" and row["proposed_by"] == "assistant"
    assert json.loads(row["context"])["status"] == "durable"
    assert [r["id"] for r in load_self_accounts(c,"aelen")["records"]] == [mid]
    assert load_self_accounts(c,"lorien")["records"] == []
    run(memory_lifecycle.cama_reject_memory(mid,"revised"))
    row=c.execute("SELECT * FROM memories WHERE id=?",(mid,)).fetchone()
    ctx=json.loads(row["context"])
    assert ctx["author"] == "aelen" and ctx["rejection_reason"] == "revised"
    assert load_self_accounts(c,"aelen")["records"] == []


def test_cap_privacy_and_attribution(fresh_db,monkeypatch):
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    ids=[]
    for i in range(5):
        mid=store(m)["memory_id"]; ids.append(mid)
        run(memory_lifecycle.cama_confirm_memory(mid))
    sensitive=store(m,consent_level="high")["memory_id"]
    run(memory_lifecycle.cama_confirm_memory(sensitive))
    records=load_self_accounts(c,"aelen")["records"]
    assert [r["id"] for r in records] == ids[-3:][::-1]
    assert sensitive not in [r["id"] for r in records]
    assert all(r["lesson"] == "Ask before assigning an ambiguous reference." for r in records)
    assert len(json.loads(budget_boot({"self_accounts":load_self_accounts(c,"aelen")},4000))["self_accounts"]["records"]) == 3
    assert matches_leaf("aelen_self_account","self_account",json.dumps({"author":"aelen","subject":"aelen"}))
    assert not matches_leaf("aelen_self_account","self_account",json.dumps({"author":"lorien","subject":"lorien"}))
    assert not matches_leaf("aelen_self_account","exchange",json.dumps({"author":"aelen","subject":"aelen"}))
    assert not matches_leaf("aelen_self_account","self_account","SELF_ACCOUNT (Aelen, about Aelen)")


def test_malformed_account_refused(fresh_db,monkeypatch):
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    result=run(memory_lifecycle.cama_store_inference(m.StoreInferenceInput(raw_text="test",memory_type="self_account")))
    assert "error" in result
    assert c.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_auto_tag_and_retag_use_author(fresh_db,monkeypatch):
    from cama.librarian.cama_retag import _membership_score
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    c.executescript("""CREATE TABLE IF NOT EXISTS librarians(id INTEGER PRIMARY KEY,name TEXT,category TEXT,routing_keywords TEXT,member_count INTEGER DEFAULT 0,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS librarian_membership(librarian_id INTEGER,memory_id INTEGER,membership_strength REAL,assigned_by TEXT,assigned_at TEXT,UNIQUE(librarian_id,memory_id));
    INSERT INTO librarians(id,name,category,routing_keywords) VALUES(70,'aelen_self_account','meta','["self_account"]');""")
    mid=store(m)["memory_id"]
    store(m,author="lorien")  # a second author must not reach Aelen's leaf
    assert [r[0] for r in c.execute("SELECT memory_id FROM librarian_membership WHERE librarian_id=70")] == [mid]
    ctx=json.dumps({"author":"lorien","subject":"lorien"})
    assert _membership_score("aelen_self_account","self_account","SELF_ACCOUNT (Aelen, about Aelen)",["self_account"],ctx)[0] == 0
    assert _membership_score("aelen_self_account","self_account","mixed CASE",[],json.dumps({"author":"aelen","subject":"aelen"}))[0] == 1


def test_stale_ring_and_explicit_review(fresh_db,monkeypatch):
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    mid=store(m)["memory_id"]
    c.execute("INSERT INTO ring(slot,memory_id,activation,last_activated_at,reason) VALUES(0,?,1,?,'legacy')",(mid,m._now()));c.commit()
    assert run(retrieval.cama_get_ring())["ring"] == []
    review=run(retrieval.cama_query_memories(m.QueryInput(query_text="SELF_ACCOUNT",filters={"memory_type":"self_account"})))
    assert mid in [r["id"] for r in review["results"]]
    assert load_self_accounts(c,"aelen")["records"] == []


def test_sensitive_account_review_is_explicit(fresh_db,monkeypatch):
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    mid=store(m,consent_level="high")["memory_id"]
    assert run(retrieval.cama_search("SELF_ACCOUNT",include_provisional=True))["results"] == []
    assert mid in [r["id"] for r in run(retrieval.cama_search("SELF_ACCOUNT",include_provisional=True,include_sensitive=True))["results"]]


def _confirmed_private_account_in_ring(c,m):
    mid=store(m)["memory_id"]
    run(memory_lifecycle.cama_confirm_memory(mid))
    c.execute("UPDATE memories SET raw_text='Aelen caught wrong SELF_ACCOUNT_PRIVATE',is_core=1 WHERE id=?",(mid,))
    c.execute("INSERT INTO ring(slot,memory_id,activation,last_activated_at,reason) VALUES(0,?,1,?,'legacy')",(mid,m._now()))
    c.commit()
    return mid

def test_alternate_sleep_summary_excludes_accounts(fresh_db,monkeypatch,tmp_path):
    from cama.sleep import cama_loop
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    _confirmed_private_account_in_ring(c,m)
    monkeypatch.setattr(cama_loop,"BOOT_SUMMARY_PATH",str(tmp_path/'boot.json'))
    cama_loop.generate_boot_summary(c)
    assert 'SELF_ACCOUNT_PRIVATE' not in (tmp_path/'boot.json').read_text()

def test_companion_context_excludes_accounts(fresh_db,monkeypatch):
    # companion_chat.py is a private, untracked module (see .gitignore); skip where absent.
    companion_chat=pytest.importorskip("companion_chat")
    c,m=fresh_db
    monkeypatch.setattr(m,"EMBEDDING_PROVIDER","none")
    _confirmed_private_account_in_ring(c,m)
    monkeypatch.setattr(companion_chat,"DB_PATH",m.DB_PATH)
    context=companion_chat.get_cama_context("SELF_ACCOUNT_PRIVATE")
    assert 'unavailable' not in context and 'SELF_ACCOUNT_PRIVATE' not in context
    assert 'unavailable' not in companion_chat.get_cama_context("quote' OR 1=1 --")
