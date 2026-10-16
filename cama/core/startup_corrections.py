"""Select attributable correction records without rewriting the archive."""

import json


def load_corrections(conn, persona, limit=3):
    """Return stored human corrections, with evidence and source labels.

    Imported exchange classifications alone are not instructions. An exchange
    needs an explicit correction annotation and a quote in its human segment.
    Legacy teachings need quoted evidence. Metadata is not authorship proof.
    """
    rows = conn.execute(
        "SELECT id,raw_text,context,evidence,source_type,proposed_by,status,created_at "
        "FROM memories WHERE memory_type='correction' AND status='durable' "
        "ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    result = []
    for row in rows:
        r = dict(row)
        try:
            context = json.loads(r['context'] or '{}')
        except (ValueError, TypeError):
            context = {}
        if not isinstance(context, dict):
            context = {}
        target = context.get('assistant_identity')
        if target and str(target).casefold() != persona.casefold():
            continue
        quote = None
        if r['source_type'] == 'teaching' and r['proposed_by'] == 'user':
            try:
                evidence = json.loads(r['evidence'] or '[]')
            except (ValueError, TypeError):
                evidence = []
            if isinstance(evidence, list):
                quote = next((e.get('quote') for e in evidence if isinstance(e, dict)
                              and isinstance(e.get('quote'), str) and e['quote'].strip()), None)
        elif r['source_type'] == 'exchange':
            annotation = context.get('correction')
            if isinstance(annotation, dict):
                candidate = annotation.get('user_quote')
                human = (r['raw_text'] or '').split('\n[ASSISTANT]', 1)[0]
                if human.startswith('[USER] ') and isinstance(candidate, str) and candidate.strip():
                    if candidate in human[len('[USER] '):]:
                        quote = candidate
        if not quote:
            continue
        result.append({key: r[key] for key in
                       ('id', 'source_type', 'proposed_by', 'status', 'created_at')})
        result[-1].update({'user_quote': quote[:800], 'excerpted': len(quote) > 800,
                           'scope': target or 'unscoped',
                           'attribution': 'stored provenance; original authorship not independently verified'})
        if len(result) >= limit:
            break
    return result
