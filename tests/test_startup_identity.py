"""Startup identity regressions; all records are synthetic."""
import json
import sqlite3

import pytest

from cama.core.startup_identity import identity_summary, load_identity, startup_instruction


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE memories (id INTEGER PRIMARY KEY, raw_text TEXT, context TEXT, source_type TEXT, proposed_by TEXT, status TEXT, created_at TEXT, memory_type TEXT)')
    yield conn
    conn.close()


def add(db, text, author='user', source='teaching', status='durable', context=None, kind='identity'):
    return db.execute('INSERT INTO memories(raw_text,context,source_type,proposed_by,status,created_at,memory_type) VALUES (?,?,?,?,?,?,?)',
                      (text,context,source,author,status,'2026-01-01',kind)).lastrowid


def test_user_identity_survives_recent_topic_churn(db):
    target = add(db, 'Your name is Lorien.')
    for i in range(150):
        add(db, f'Lorien worked on task {i}', kind='experience')
    identity = load_identity(db, 'Lorien')
    assert [r['id'] for r in identity['teachings']] == [target]
    assert f'User teaching [{target}]' in identity_summary(identity)
    assert identity['teachings'][0]['source_type'] == 'teaching'


def test_other_persona_and_legacy_assistant_claims_are_not_identity(db):
    add(db, 'Lorien told Aelen to help', author='aelen', source='inference')
    add(db, 'Lorien is awake', author='assistant', source='inference')
    add(db, 'Lorien is awake', author='aelen', source='teaching')
    add(db, 'Lorienette is the name')
    assert load_identity(db, 'Lorien')['status'] == 'missing'


@pytest.mark.parametrize('status', ['expired', 'rejected', 'provisional'])
def test_ineligible_teachings_not_loaded(db, status):
    add(db, 'You are Lorien', status=status)
    assert load_identity(db, 'Lorien')['teachings'] == []


def test_provisional_self_assessment_stays_separate(db):
    mid = add(db, 'I aim to be clear', author='assistant', source='inference', status='provisional', context=json.dumps({'assistant_identity':'Lorien'}))
    r = load_identity(db, 'Lorien')
    assert not r['teachings']
    assert r['self_assessments'][0]['id'] == mid
    assert r['self_assessments'][0]['status'] == 'provisional'
    assert identity_summary(r) == ''


@pytest.mark.parametrize('context', ['broken', '[]', 'null'])
def test_legacy_context_does_not_break_boot(db, context):
    add(db, 'Your name is Lorien.', context=context)
    assert load_identity(db, 'Lorien')['status'] == 'available'


@pytest.mark.parametrize('context', ['broken', '[]', 'null'])
def test_malformed_context_does_not_promote_incidental_identity_mentions(db, context):
    add(db, 'Lorien identity', context=context)
    assert load_identity(db, 'Lorien')['status'] == 'missing'


def test_missing_journal_is_reported_honestly():
    assert 'No eligible journal loaded.' in startup_instruction('Lorien', {'note':'No journal'})
    assert 'Journal loaded.' not in startup_instruction('Lorien', {'note':'No journal'})
    assert 'Journal loaded.' in startup_instruction('Lorien', {'memory_id':1})


def test_actual_boot_reader_uses_database_identity(fresh_db, tmp_path, monkeypatch):
    from cama.core.boot_file import write_boot
    from mcp_sections import continuity
    conn, mcp = fresh_db
    monkeypatch.setenv('CAMA_BOOT_SUMMARY', str(tmp_path/'boot.json'))
    monkeypatch.setattr(continuity, 'PERSONA_NAME', 'Lorien')
    conn.execute("INSERT INTO memories(raw_text,memory_type,source_type,proposed_by,status,created_at,updated_at) VALUES ('You are Lorien','identity','teaching','user','durable',?,?)", (mcp._now(),mcp._now()))
    conn.commit()
    write_boot({'generated_at':mcp._now(), 'identity':[{'text':'other persona'}]})
    boot = continuity._read_boot_context(conn)
    assert boot['status'] == 'fresh'
    assert 'You are Lorien' in boot['identity_summary']
    assert 'other persona' not in boot['identity_summary']
    assert boot['identity']['persona'] == 'Lorien'
