"""Public synthetic regressions for September 30 continuity failures."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from cama.core.startup_corrections import load_corrections
from cama.ingest.cama_import_claude_code import build_memory
from cama.librarian.cama_auto_tag import _route_memory_to_librarians, tag_memory
from mcp_sections import retrieval

try:
    import cama_check_self as checks  # private, untracked module (see .gitignore)
except ImportError:  # pragma: no cover - absent in CI and fresh clones
    checks = None
needs_check_self = pytest.mark.skipif(checks is None, reason="cama_check_self is a private, untracked module")


def add(conn, mcp, text, *, kind='exchange', source='exchange', author='system', context=None,
        evidence='[]', status='durable'):
    return conn.execute(
        'INSERT INTO memories(raw_text,memory_type,source_type,proposed_by,status,context,evidence,'
        'created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (text, kind, source, author, status, context, evidence, mcp._now(), mcp._now()),
    ).lastrowid


def test_type_index_is_structural_not_keyword(fresh_db):
    conn, mcp = fresh_db
    conn.executescript('''
        CREATE TABLE librarians(id INTEGER PRIMARY KEY,name TEXT,category TEXT,routing_keywords TEXT,
                                member_count INTEGER DEFAULT 0,updated_at TEXT);
        CREATE TABLE librarian_membership(librarian_id INTEGER,memory_id INTEGER,membership_strength REAL,
                                          assigned_by TEXT,assigned_at TEXT,
                                          PRIMARY KEY(librarian_id,memory_id));
    ''')
    for name, kw in [('type_exchange', 'exchange'), ('type_teaching', 'teaching'),
                     ('type_correction', 'correction'), ('topic_memory', 'memory')]:
        conn.execute('INSERT INTO librarians(name,category,routing_keywords) VALUES (?,?,?)',
                     (name,'topic',json.dumps([kw])))
    text = 'A teaching about correction and memory is being discussed.'
    mid = add(conn, mcp, text)
    result = tag_memory(conn, mid, text)
    assert set(result['tagged_to']) == {'type_exchange', 'topic_memory'}
    assert conn.execute('SELECT source_type FROM memories WHERE id=?',(mid,)).fetchone()[0] == 'exchange'
    assert all(not r[1].startswith('type_') for r in _route_memory_to_librarians(conn, text))


def test_imported_coursework_and_apology_remain_exchange():
    row = build_memory({'user_text': 'Question 14. Correct the error in this assignment.',
                        'assistant_text': "I was wrong. This is a teaching and identity breakthrough.",
                        'timestamp': '2026-01-01T12:00:00Z', 'session_id': 'synthetic',
                        'source_msg_id': 'synthetic:1', 'entrypoint': 'synthetic'})
    assert row['memory_type'] == row['source_type'] == 'exchange'
    assert row['raw_text'].startswith('[USER] Question 14.')


def test_bulk_retag_uses_the_same_structural_type_rule():
    from cama.librarian.cama_retag import _membership_score
    assert _membership_score('type_teaching','exchange','teaching',['teaching'])[0] == 0
    assert _membership_score('type_exchange','exchange','ordinary text',[])[0] == 1
    assert _membership_score('topic_memory','exchange','memory',['memory'])[0] == 1


def test_startup_corrections_preserve_attribution_and_ignore_coursework(fresh_db):
    conn, mcp = fresh_db
    add(conn,mcp,'[USER] Question 14\n[ASSISTANT] I was wrong.',kind='correction')
    add(conn,mcp,'A guess attributed to the human',kind='correction',source='teaching',author='assistant')
    add(conn,mcp,'A correction without evidence',kind='correction',source='teaching',author='user')
    quote='Please use my chosen name.'
    good=add(conn,mcp,quote,kind='correction',source='teaching',author='user',
             evidence=json.dumps([{'quote':quote}]),context=json.dumps({'assistant_identity':'Nova'}))
    result=load_corrections(conn,'Nova')
    assert [r['id'] for r in result] == [good]
    assert result[0]['user_quote'] == quote
    assert result[0]['source_type'] == 'teaching'
    assert load_corrections(conn,'Other') == []


def test_exchange_correction_requires_quote_in_human_segment(fresh_db):
    conn,mcp=fresh_db
    text='[USER] Use short paragraphs.\n[ASSISTANT] Invented instruction.'
    add(conn,mcp,text,kind='correction',context=json.dumps({'correction':{'user_quote':'Invented instruction.'}}))
    good=add(conn,mcp,text,kind='correction',context=json.dumps({'correction':{'user_quote':'Use short paragraphs.'}}))
    assert [r['id'] for r in load_corrections(conn,'Nova')] == [good]


@pytest.mark.parametrize('budget',[2000,24000])
def test_ring_is_bounded_with_coverage_and_originals_intact(fresh_db,monkeypatch,budget):
    conn,mcp=fresh_db
    monkeypatch.setattr(retrieval,'_session_tick',lambda:None)
    original='Memory '+('\U0001f331' * 20000)
    ids=[]
    for slot in range(5):
        mid=add(conn,mcp,original,context='x'*10000,evidence='y'*10000)
        ids.append(mid)
        mcp._ring_push(conn,mid,'synthetic')
    conn.commit()
    response=asyncio.run(retrieval.cama_get_ring(max_chars=budget))
    result=json.loads(response)
    assert len(response.encode('utf-8')) <= budget
    assert {r['id'] for r in result['ring']} | set(result['coverage']['omitted_ids']) == set(ids)
    assert result['coverage']['shown'] == len(result['ring'])
    for r in result['ring']:
        assert r['source_type'] == 'exchange'
        assert r['proposed_by'] == 'system'
        assert 'raw_text' in r['excerpted_fields']
    assert conn.execute('SELECT raw_text FROM memories WHERE id=?',(ids[0],)).fetchone()[0] == original


def test_ring_excludes_quarantined_records(fresh_db,monkeypatch):
    conn,mcp=fresh_db
    monkeypatch.setattr(retrieval,'_session_tick',lambda:None)
    mid=add(conn,mcp,'quarantined',status='durable')
    mcp._ring_push(conn,mid,'synthetic')
    conn.execute("UPDATE memories SET status='quarantined' WHERE id=?",(mid,));conn.commit()
    assert json.loads(asyncio.run(retrieval.cama_get_ring()))['ring'] == []


@pytest.mark.parametrize('text',['CAMA is ready.','The BEST plan.','AM PM EST','The sample is 99:99 PM.'])
@needs_check_self
def test_timestamp_gate_does_not_accept_word_substrings(text):
    result=checks.gate_4_standing_protocol(text,datetime.now(timezone.utc).isoformat(),0,None)
    assert not result.passed
    assert 'no_timestamp_in_response' in result.reasoning


@needs_check_self
def test_visible_timestamp_without_clock_receipt_is_flagged():
    result=checks.gate_4_standing_protocol('Ready at 9:30 AM.',None,0,None)
    assert 'FABRICATED_TIMESTAMP' in result.reasoning


@needs_check_self
def test_timestamp_argument_must_actually_appear():
    result=checks.gate_4_standing_protocol('CAMA is ready.',datetime.now(timezone.utc).isoformat(),0,'9:30 AM')
    assert 'timestamp_argument_not_in_response' in result.reasoning


@pytest.mark.parametrize('stamp',['9:30 AM','14:30 UTC','2026-09-30T14:30:00Z'])
@needs_check_self
def test_valid_visible_timestamp_with_fresh_receipt(stamp):
    assert checks.gate_4_standing_protocol(stamp,datetime.now(timezone.utc).isoformat(),0,stamp).passed


@needs_check_self
def test_check_self_uses_shared_receipt_without_refreshing(monkeypatch):
    now=datetime.now(timezone.utc).isoformat()
    monkeypatch.setitem(checks.sup._session,'last_timestamp_at',now)
    kwargs=dict(proposed_response='9:30 AM. The test passed.',user_message='Status?',
                recent_exchanges=[{'user':'Status?','assistant':'Checking.'}],ring_loaded=True,log=False)
    result=checks.check_self(**kwargs)
    gate=next(g for g in result.gates if g.gate_name=='standing_protocol')
    assert gate.passed
    assert checks.sup._session['last_timestamp_at']==now
    old=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    result=checks.check_self(**kwargs,last_timestamp_iso=old)
    assert not next(g for g in result.gates if g.gate_name=='standing_protocol').passed


@pytest.mark.parametrize('kind',['preference','boundary','pattern','insight','relational'])
def test_repeated_hive_observations_never_acquire_human_authority(fresh_db,monkeypatch,kind):
    from cama.hive import cama_hive as hive
    conn,mcp=fresh_db
    monkeypatch.setattr(hive,'DB_PATH',mcp.DB_PATH)
    conn.commit()
    for _ in range(3):
        nectar=hive.add_nectar('Synthetic repeated observation',[123],honey_type=kind)
    result=hive.crystallize_honey(nectar['honey_id'])
    row=conn.execute('SELECT * FROM memories WHERE id=?',(result['memory_id'],)).fetchone()
    assert row['status']=='provisional'
    assert row['source_type']=='inference'
    assert row['proposed_by']=='hive_distiller'
    assert row['needs_user_confirmation']==1
    assert row['review_after'] is not None
    assert row['confidence']<1
    assert 'memory:123' in row['evidence']
    assert hive.crystallize_honey(nectar['honey_id']).get('error')
