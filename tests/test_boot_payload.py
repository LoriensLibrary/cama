"""Synthetic transport tests; no personal CAMA or Claude settings required."""
import json

import pytest

from cama.core.boot_payload import budget_boot


def test_oversized_boot_keeps_correction_and_provenance_as_valid_json():
    original={'journal':{'note':'No eligible journal loaded.'},
              'boot':{'identity':{'teachings':[{'id':1,'source_type':'teaching','proposed_by':'user',
                       'created_at':'2026-01-01','text':'x'*40000}]*8}},
              'corrections':[{'id':99,'source_type':'teaching','proposed_by':'user',
                              'user_quote':'Please use short paragraphs.'}],
              'instruction':'Keep authorship separate.',
              'resonant_memories':[{'id':i,'text':'\U0001f331'*5000} for i in range(30)]}
    encoded=budget_boot(original)
    result=json.loads(encoded)
    assert len(encoded.encode('utf-8'))<=16000
    assert result['corrections'][0]['id']==99
    assert result['corrections'][0]['proposed_by']=='user'
    assert result['journal']['note']=='No eligible journal loaded.'
    assert 'memory_id' not in result['journal']
    assert result['coverage']['sections']['identity']['status']=='trimmed'
    assert len(original['boot']['identity']['teachings'][0]['text'])==40000


def test_identity_teachings_survive_trimming():
    teachings=[{'id':i,'source_type':'teaching','proposed_by':'user','text':'t'*180} for i in range(10)]
    original={'instruction':'x','boot':{'identity':{'status':'available','teachings':teachings},
                                        'recent_topics':[{'text':'r'*900}]*20},
              'resonant_memories':[{'id':i,'text':'m'*2000} for i in range(30)]}
    result=json.loads(budget_boot(original))
    assert [t['id'] for t in result['identity']['teachings']]==list(range(10))
    assert result['coverage']['sections']['identity']['status']=='included'
    assert 'identity' in original['boot']


def test_small_boot_remains_complete():
    original={'instruction':'Read source labels.','corrections':[],'boot':{'status':'fresh'}}
    result=json.loads(budget_boot(original))
    assert {k:result[k] for k in original}==original
    assert all(v['status']=='included' for v in result['coverage']['sections'].values())


@pytest.mark.parametrize('payload',['[]','null','{broken'])
def test_invalid_boot_is_reported_instead_of_sliced(payload):
    with pytest.raises(ValueError):
        budget_boot(payload)


def test_small_continuity_sections_survive_an_over_budget_boot():
    """Review finding K-5: aelen_state, hive and brain_layers were the first
    sections dropped on every over-budget boot. They are reserved whole now."""
    original={'instruction':'x',
              'identity':{'teachings':[{'id':i,'source_type':'teaching','proposed_by':'user','text':'t'*300} for i in range(12)]},
              'pending_confirmations':[{'id':i,'text':'p'*200} for i in range(12)],
              'journal':{'entry':'j'*3000},
              'resonant_memories':[{'id':i,'text':'m'*1500} for i in range(12)],
              'hive':{'hive_active':True,'processing_modifiers':{'response_style':{'signal':'gentle','intensity':0.4}}},
              'aelen_state':{'emotional_state':'steady','last_journal_memory_id':'52910'},
              'brain_layers':'insights: 3 open; self-model: stable; intent: finish the review'}
    encoded=budget_boot(original)
    result=json.loads(encoded)
    assert len(encoded.encode('utf-8'))<=16000
    for key in ('hive','aelen_state','brain_layers'):
        assert result[key]==original[key], key
        assert result['coverage']['sections'][key]['status']=='included', key
    assert result['coverage']['sections']['resonant_memories']['status'] in ('trimmed','dropped')


def test_oversized_protected_section_falls_back_to_the_ladder():
    original={'instruction':'x','journal':{'entry':'j'*20000},
              'brain_layers':'b'*5000}
    result=json.loads(budget_boot(original))
    assert result['coverage']['sections']['brain_layers']['status'] in ('trimmed','included')
    assert len(json.dumps(result['brain_layers']))<=700
