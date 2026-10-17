"""Reproduce continuity selection with synthetic data, without a personal DB.

Run: python -m cama.eval.continuity_demo --output results.json
This is a deterministic software check, not an LLM or subjective-identity benchmark.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from cama.core.startup_corrections import load_corrections
from cama.core.startup_identity import load_identity
from cama.librarian.cama_auto_tag import _route_memory_to_librarians


def run():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE memories(id INTEGER PRIMARY KEY,raw_text TEXT,context TEXT,
            source_type TEXT,proposed_by TEXT,status TEXT,created_at TEXT,memory_type TEXT,evidence TEXT);
        CREATE TABLE librarians(id INTEGER PRIMARY KEY,name TEXT,category TEXT,routing_keywords TEXT);
    ''')

    def add(text,kind='identity',source='teaching',author='user',status='durable',context=None,evidence=None):
        return conn.execute('INSERT INTO memories(raw_text,context,source_type,proposed_by,status,'
                            'created_at,memory_type,evidence) VALUES (?,?,?,?,?,?,?,?)',
                            (text,json.dumps(context or {}),source,author,status,'2026-01-01',kind,
                             json.dumps(evidence or []))).lastrowid

    named=add('Your name is Nova.')
    for number in range(150):
        add(f'Nova worked on task {number}.',kind='experience')
    add('Nova appeared in a job description.')
    add('Your name is Orion.')
    assessment=add('I aim to be concise.',source='inference',author='assistant',status='provisional',
                   context={'assistant_identity':'Nova'})
    add('Your name is Nova.',status='rejected')
    correction=add('Please use short paragraphs.',kind='correction',
                   context={'assistant_identity':'Nova'},evidence=[{'quote':'Please use short paragraphs.'}])
    add('[USER] Question 14\n[ASSISTANT] I was wrong.',kind='correction',source='exchange',author='system')
    for name in ('type_exchange','type_teaching','type_correction'):
        conn.execute('INSERT INTO librarians(name,category,routing_keywords) VALUES (?,?,?)',
                     (name,'topic',json.dumps([name[5:]])))
    identity=load_identity(conn,'Nova')
    corrections=load_corrections(conn,'Nova')
    routing=_route_memory_to_librarians(conn,'teaching correction',memory_type='exchange')
    cases = {
        'explicit_identity_survives_topic_churn': [r['id'] for r in identity['teachings']]==[named],
        'self_assessment_stays_provisional': identity['self_assessments'][0]['id']==assessment
            and identity['self_assessments'][0]['status']=='provisional',
        'other_persona_excluded': all('Orion' not in r['text'] for r in identity['teachings']),
        'human_correction_retained': [r['id'] for r in corrections]==[correction],
        'correction_keeps_authorship': corrections[0]['proposed_by']=='user',
        'coursework_not_standing_guidance': len(corrections)==1,
        'correction_scoped_to_persona': load_corrections(conn,'Orion')==[],
        'type_index_uses_metadata': [r[1] for r in routing]==['type_exchange'],
    }
    conn.close()
    return {'fixture':'synthetic-only','scope':'deterministic continuity selection; no model generation',
            'cases':cases,'passed':sum(cases.values()),'total':len(cases)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=run()
    encoded=json.dumps(report,indent=2)
    if args.output:
        args.output.write_text(encoded+'\n',encoding='utf-8')
    print(encoded)
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
