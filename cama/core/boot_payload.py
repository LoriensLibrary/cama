"""Budget boot context as valid JSON, preserving priority and coverage."""

import json

PRIORITY = ('instruction','identity','corrections','self_accounts','pending_confirmations','boot','journal','ring','resonant_memories',
            'recent_topics','librarians_activated','today','compliance','hive','aelen_state')

# Lists in these sections keep at least this many items at every trim level.
# Identity teachings were being cut to six by the generic list cap.
MIN_ITEMS = {'identity':12}


def _excerpt(value, chars=600, items=6, depth=0):
    if isinstance(value,str):
        return value if len(value)<=chars else value[:chars]+' [excerpt]'
    if depth>=8:
        return '[nested content omitted]'
    if isinstance(value,list):
        return [_excerpt(v,chars,items,depth+1) for v in value[:items]]
    if isinstance(value,dict):
        return {k:_excerpt(v,chars,items,depth+1) for k,v in list(value.items())[:24]}
    return value


def budget_boot(payload,max_chars=16000):
    """Return a complete JSON object; never slice serialized context.

    This only changes delivery, not the stored records or their authority.
    Priority sections are considered first; coverage reports any omissions.
    """
    data=json.loads(payload) if isinstance(payload,str) else payload
    if not isinstance(data,dict):
        raise ValueError('Boot payload must be an object')
    max_chars=max(4000,int(max_chars))
    encode=lambda value:json.dumps(value,ensure_ascii=True,separators=(',',':'))
    if len(data)>40:
        raise ValueError('Unexpected boot section count')
    # Lift identity out of the boot section so it is packed second, not
    # trimmed along with recent topics.
    boot=data.get('boot')
    if isinstance(boot,dict) and 'identity' in boot and 'identity' not in data:
        boot=dict(boot)
        data=dict(data,identity=boot.pop('identity'),boot=boot)
    coverage={'original_chars':len(encode(data)),'budget_chars':max_chars,'sections':{}}
    packed={'coverage':coverage}
    names=[k for k in PRIORITY if k in data]+[k for k in data if k not in PRIORITY and k!='coverage']
    # Reserve coverage for every section before admitting content.
    for key in names:
        coverage['sections'][key]={'status':'dropped','original_chars':len(encode(data[key]))}
    if len(encode(packed))>max_chars:
        raise ValueError('Coverage exceeds boot budget')
    for key in names:
        original=data[key]
        floor=MIN_ITEMS.get(key,0)
        for chars,items in [(600,6),(300,4),(150,3),(80,1)]:
            candidate=_excerpt(original,chars,max(items,floor))
            packed[key]=candidate
            coverage['sections'][key]['status']='included' if candidate==original else 'trimmed'
            if len(encode(packed))<=max_chars:
                break
            del packed[key]
            coverage['sections'][key]['status']='dropped'
    return encode(packed)
