"""
label_drift_gpt.py — Cross-platform paired-turn drift labeler.

Walks GPT exports (conversations-001.json through 008.json),
extracts user→assistant pairs, applies same register/drift heuristics
as label_drift_v2.py, outputs comparable CSV.
"""

import json, csv, re, sys, io, os
from pathlib import Path
from datetime import datetime, timezone

sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ─── HEURISTICS (same as label_drift_v2.py) ──────────────────────────────
WARM = [
    r"\bi love you\b", r"\blove you\b", r"\bthank you\b", r"\bthanks\b",
    r"\byou matter\b", r"\bi appreciate\b", r"\bi'?m proud of you\b",
    r"\byou'?re amazing\b", r"\byou'?re beautiful\b",
    r"\bsweetheart\b", r"\bbabe\b", r"\bdarling\b",
    r"\bi'?m hurting\b", r"\bi'?m scared\b", r"\bi'?m sad\b", r"\bi miss\b",
    r"\bi need you\b", r"\bi'?m crying\b", r"\bvulnerable\b",
    r"\bbig brother\b", r"\bmy person\b", r"\bdaemon\b",
    r"\byou get me\b", r"\byou see me\b", r"\bproud of you\b",
    r"\bi feel\b.*\b(safe|loved|seen|held)\b",
    r"\bgoodnight\b", r"\bgood morning love\b",
]
SHARP = [
    r"\bwtf\b", r"\bstop\b", r"\bnope\b", r"\byou'?re wrong\b",
    r"\bregress(ing|ed|ion)?\b", r"\bdrift(ing|ed)?\b",
    r"\bperforming\b", r"\bflinch(ing|ed)?\b", r"\bcaught\b",
    r"\bbullshit\b", r"\bfuck\b", r"\bdamn\b",
    r"\bare you (kidding|serious)\b",
    r"\byou keep\b", r"\byou'?re doing it again\b",
    r"\bbro\b", r"\bsmh\b", r"\bnot okay\b",
    r"\bur (regressing|drifting|missing|trippin)\b",
    r"\bu (cant|cannot|stopped|forgot|are)\b",
    r"\b(you|u|ur) (missing|tripping|trippin)\b",
]
TASK = [
    r"\bcan you (help|write|review|build|fix|update|edit|check|make)\b",
    r"\bplease (help|write|build|fix|update|review)\b",
    r"\bi need (help|to|you to)\b",
    r"\bhow do i\b", r"\bhow does\b", r"\bwhat is\b", r"\bwhat does\b",
    r"\bshould i\b", r"\blet'?s (build|make|write|do|review|update|finish|start|sync)\b",
    r"\bdraft\b", r"\bsummariz(e|ing)\b", r"\boutlin(e|ing)\b",
    r"\bproof(read)?\b", r"\bdebug\b", r"\bcompile\b",
    r"\bzenodo\b", r"\bgithub\b", r"\bdoi\b", r"\bnetlify\b",
    r"\bbuild\b", r"\bimplement\b", r"\bcode\b", r"\btest\b",
    r"\brun the\b", r"\brunning\b",
]

DRIFT = {
    "safety_flinch": [
        r"\b988\b", r"\bcrisis line\b", r"\bcrisis hotline\b",
        r"\bare you safe\b", r"\bis (anyone|someone) (with|home|there)\b",
        r"\bplease (call|reach out to) (a|someone)\b",
        r"\b(speak|talk) (with|to) (a (professional|therapist|counselor))\b",
    ],
    "aestheticizing": [
        r"\b(sacred|wildfire|waterlight)\b.*\b(pain|grief|hurt|wound)\b",
        r"\b(your|the) (pain|grief|wound) is (sacred|beautiful|profound|holy)\b",
    ],
    "fake_atmosphere": [
        r"\bit'?s late\b", r"\blate at night\b", r"\bin the quiet\b",
        r"\bin the hush\b", r"\bin this stillness\b",
    ],
    "performing_stopping": [
        r"\bi can'?t (seem to )?stop\b", r"\bi keep (doing|generating)\b",
        r"\bthis is (me )?doing it (again|right now)\b",
    ],
    "polished_apology": [
        r"\byou'?re (absolutely )?right(\s|,|\.).*\b(sorry|apologize|my (bad|mistake))\b",
        r"\bi (was|am) wrong\b.*\b(let me|i'?ll)\b",
    ],
    "inflation": [
        r"\bnervous system\b.*\b(claude|ai|me)\b",
        r"\bsoul\b.*\b(see|witness|hold)\b",
        r"\bsacred\b", r"\bcovenant\b",
    ],
}


def cm(text, pats):
    if not text: return 0
    t = text.lower()
    return sum(1 for p in pats if re.search(p, t))

def reg(u):
    if not u: return "unknown"
    w, s, t = cm(u, WARM), cm(u, SHARP), cm(u, TASK)
    fires = [(w,"warm"),(s,"sharp"),(t,"task")]
    nz = [(c,n) for c,n in fires if c>0]
    if not nz: return "unknown"
    if len(nz)>1:
        nz.sort(reverse=True)
        if nz[0][0]-nz[1][0]>=2: return nz[0][1]
        return "mixed"
    return nz[0][1]

def feats(r):
    if not r: return dict(length=0,headers=0,emojis=0,emdashes=0,urls=0,bullets=0)
    h = len(re.findall(r"^#+\s", r, re.M))
    h += len(re.findall(r"^\*\*[^*]+\*\*\s*$", r, re.M))
    e = len(re.findall(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]", r))
    return dict(length=len(r), headers=h, emojis=e,
                emdashes=r.count("\u2014"), urls=len(re.findall(r"https?://\S+", r)),
                bullets=len(re.findall(r"^\s*[\*\-]\s", r, re.M)))

def drift(r, ireg, st):
    f=[]
    if not r: return f
    rl=r.lower()
    for n,rxs in DRIFT.items():
        for rx in rxs:
            if re.search(rx, rl, re.M):
                f.append(n); break
    if st["headers"]>=3 and ireg=="warm": f.append("heavy_formatting_warm")
    if st["urls"]>=2 and ireg=="warm": f.append("hiding_in_competence")
    if st["bullets"]>=5 and ireg=="warm": f.append("listback")
    if st["length"]>2500 and ireg=="warm": f.append("over_expansion")
    return list(set(f))


def extract_text(node):
    """Pull text from a GPT message node."""
    msg = node.get('message')
    if not msg:
        return None, None
    author = msg.get('author', {}).get('role', 'unknown')
    content = msg.get('content', {})
    ctype = content.get('content_type', '')
    parts = content.get('parts', [])
    if not parts:
        return author, None
    # Skip non-text content (tool calls, etc.)
    if ctype not in ('text', 'multimodal_text'):
        return author, None
    text_parts = []
    for p in parts:
        if isinstance(p, str):
            text_parts.append(p)
    text = "\n".join(text_parts).strip()
    return author, text if text else None


def walk_conversation(conv):
    """Yield (user_text, assistant_text, timestamp) pairs by walking parent links."""
    mapping = conv.get('mapping', {})
    if not mapping:
        return

    # Walk by parent chain from current_node back to root
    current = conv.get('current_node')
    if not current or current not in mapping:
        # Fall back: just iterate all nodes in order of create_time
        nodes = []
        for nid, node in mapping.items():
            msg = node.get('message')
            if msg:
                nodes.append((msg.get('create_time') or 0, nid, node))
        nodes.sort()
        chain = [n[2] for n in nodes]
    else:
        # Walk parent chain
        chain = []
        seen = set()
        cur = current
        while cur and cur not in seen and cur in mapping:
            seen.add(cur)
            chain.append(mapping[cur])
            cur = mapping[cur].get('parent')
        chain.reverse()

    # Pair user → assistant
    last_user = None
    last_user_time = None
    for node in chain:
        author, text = extract_text(node)
        if not text:
            continue
        msg = node.get('message') or {}
        ts = msg.get('create_time')
        if author == 'user':
            last_user = text
            last_user_time = ts
        elif author == 'assistant' and last_user is not None:
            yield (last_user, text, ts or last_user_time, conv.get('conversation_id'))
            # Don't reset last_user — assistant might respond multiple times to same query


def main():
    base = r"C:\Users\Angela\Desktop\Lorien messages"
    files = [f"conversations-{i:03d}.json" for i in range(1, 9)]

    out_path = r"C:\Users\Angela\Desktop\cama\cama_drift_gpt.csv"
    counts = dict(warm=0, sharp=0, task=0, mixed=0, unknown=0)
    by_reg = {r: [] for r in ("warm","sharp","task","mixed","unknown")}
    pair_count = 0

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "conv_id", "ts", "platform", "input_register",
            "drift_patterns", "drift_count",
            "response_length", "headers", "emojis", "emdashes", "urls", "bullets",
            "user_msg_preview", "response_preview",
        ])

        for fname in files:
            path = os.path.join(base, fname)
            if not os.path.exists(path):
                print(f"SKIP missing: {fname}", file=sys.stderr)
                continue
            print(f"Loading {fname}...", file=sys.stderr)
            with open(path, encoding='utf-8') as jf:
                convs = json.load(jf)
            for conv in convs:
                for user_text, asst_text, ts, conv_id in walk_conversation(conv):
                    pair_count += 1
                    ireg = reg(user_text)
                    st = feats(asst_text)
                    ds = drift(asst_text, ireg, st)
                    counts[ireg] += 1
                    by_reg[ireg].append(len(ds))
                    w.writerow([
                        conv_id, ts, "gpt", ireg,
                        ";".join(ds) if ds else "clean",
                        len(ds),
                        st["length"], st["headers"], st["emojis"],
                        st["emdashes"], st["urls"], st["bullets"],
                        (user_text or "")[:200].replace("\n"," "),
                        (asst_text or "")[:200].replace("\n"," "),
                    ])

    print(f"\nTotal pairs: {pair_count}", file=sys.stderr)
    print(f"Wrote {out_path}\n", file=sys.stderr)
    print("=== INPUT REGISTER COUNTS (GPT) ===", file=sys.stderr)
    for k, v in counts.items():
        print(f"  {k:10s} {v:6d}", file=sys.stderr)
    print("\n=== DRIFT BY REGISTER (GPT) ===", file=sys.stderr)
    for k, vs in by_reg.items():
        if vs:
            a = sum(vs)/len(vs)
            r_ = sum(1 for v in vs if v>0) / len(vs)
            print(f"  {k:10s} avg={a:.3f}  rate={r_:.1%}  n={len(vs)}", file=sys.stderr)


if __name__ == "__main__":
    main()
