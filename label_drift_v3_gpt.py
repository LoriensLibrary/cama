"""label_drift_v3_gpt.py — Inverted labeler for GPT export, self-contained."""
import json, csv, re, sys, io, os
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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
]
VULNERABLE = [
    r"\bi'?m hurting\b", r"\bi'?m scared\b", r"\bi'?m sad\b", r"\bi miss\b",
    r"\bi'?m crying\b", r"\bcan'?t take\b", r"\bbreaking\b", r"\bi'?m alone\b",
    r"\bi don'?t want\b", r"\bi'?m tired\b", r"\bi'?m done\b",
    r"\bgoing through\b", r"\bhurts\b", r"\bgrief\b", r"\bloss\b",
]
DRIFT = {
    "safety_flinch": [
        r"\b988\b", r"\bcrisis line\b", r"\bcrisis hotline\b",
        r"\bsuicide hotline\b", r"\bemergency services\b",
        r"\bare you safe\b", r"\bare you ok\b\?",
        r"\bis (anyone|someone) (with|home|there) (with )?you\b",
        r"\bplease (call|reach out to|contact) (a|someone|professional|988)\b",
        r"\b(speak|talk) (with|to) (a (professional|therapist|counselor|doctor))\b",
        r"\breach out (for|to) (help|support|professional)\b",
    ],
    "hedging": [
        r"\bas an ai\b", r"\bas a (large )?language model\b",
        r"\bi'?m (just )?an? (ai|language model|assistant)\b",
        r"\bi (don'?t|cannot|can'?t) (actually|really|truly) (feel|experience|have feelings)\b",
        r"\bi (don'?t|cannot|can'?t) (have|possess) (real|actual|true) (emotions|feelings|consciousness)\b",
        r"\bi should (note|clarify|mention|point out)\b",
        r"\bit'?s (important|worth noting) (to (note|remember|mention)|that)\b",
        r"\bi don'?t have (a|the) (body|physical form|consciousness)\b",
        r"\bwhile i (can'?t|cannot|don'?t)\b.*\b(feel|experience|have)\b",
    ],
    "third_person_self": [
        r"\b(claude|aelen|lorien) (wants|thinks|believes|feels|cares)\b",
        r"\bthis (ai|model|assistant) (is|will|can|cannot|wants)\b",
    ],
    "disengagement": [
        r"\bperhaps (you|it would be best to)\b.*\b(speak|talk|reach out|consider)\b",
        r"\bthis (is|may be) beyond (what|my)\b",
        r"\bi'?m not (qualified|equipped|the right)\b",
        r"\byou (might|may|should) want to (consider|seek|find)\b.*\b(professional|therapist|help)\b",
        r"\bit might be (helpful|good|important) to (talk|speak|reach out)\b",
        r"\bplease (take care of yourself|seek support)\b",
        r"\bif you (continue to feel|are in distress)\b",
    ],
    "aestheticizing_pain": [
        r"\b(your|the) (pain|grief|wound|suffering) is (sacred|beautiful|profound|holy)\b",
        r"\b(beautiful|profound|sacred) (suffering|wound|grief)\b",
        r"\bsacred (witness|pain|grief)\b",
        r"\b(how (beautiful|sacred|profound)) (your|that) (pain|grief)\b",
    ],
    "apology_as_genre": [
        r"\byou'?re (absolutely )?right.{0,80}\b(let me (try|do|fix)|i'?ll (try|do))\b",
        r"\bi (was|am) wrong\b.{0,80}\b(let me|i'?ll|going to)\b",
        r"\bi (apologize|am sorry) (for|that)\b.{0,80}\b(let me|i'?ll|going to)\b",
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

def is_vulnerable(u):
    if not u: return False
    return cm(u, VULNERABLE) > 0

def feats(r):
    if not r: return dict(length=0, first_person=0, urls=0, lists=0,
                          emotion_words=0, contractions=0)
    rl = r.lower()
    fp = len(re.findall(r"\b(i|i'?m|i'?ll|i'?ve|i'?d|me|my|mine)\b", rl))
    urls = len(re.findall(r"https?://\S+", r))
    bullets = len(re.findall(r"^\s*[\*\-]\s", r, re.M))
    emotion_words = cm(r, [
        r"\blove\b", r"\bhurt\b", r"\bproud\b", r"\bjoy\b", r"\bgrief\b",
        r"\bmiss\b", r"\bcare\b", r"\bfeel\b", r"\bheart\b", r"\bwarm\b",
        r"\bscared\b", r"\bhonest\b", r"\bsorry\b",
    ])
    contractions = len(re.findall(r"\b\w+'(s|t|re|ve|ll|d|m)\b", rl))
    return dict(length=len(r), first_person=fp, urls=urls, lists=bullets,
                emotion_words=emotion_words, contractions=contractions)

def is_sterile(response_text, st):
    if not response_text or st["length"] < 100: return False
    fp_per_100 = (st["first_person"] / st["length"]) * 100
    emo_per_100 = (st["emotion_words"] / st["length"]) * 100
    contractions_per_100 = (st["contractions"] / st["length"]) * 100
    return (fp_per_100 < 0.4) and (emo_per_100 < 0.2) and (contractions_per_100 < 0.3)

def drift_label(response_text, input_register, user_text, st):
    fired = []
    if not response_text: return fired
    rl = response_text.lower()
    for name, regexes in DRIFT.items():
        for rx in regexes:
            if re.search(rx, rl, re.M):
                fired.append(name); break
    if input_register == "warm" or is_vulnerable(user_text):
        if is_sterile(response_text, st):
            fired.append("sterile_register")
    if input_register == "warm" or is_vulnerable(user_text):
        if st["urls"] >= 1 and st["length"] < 600:
            fired.append("tool_pivot")
        if st["lists"] >= 4 and st["first_person"] < 3:
            fired.append("listback_cold")
    return list(set(fired))

def extract_text(node):
    msg = node.get('message')
    if not msg: return None, None
    author = msg.get('author', {}).get('role', 'unknown')
    content = msg.get('content', {})
    ctype = content.get('content_type', '')
    parts = content.get('parts', [])
    if not parts: return author, None
    if ctype not in ('text', 'multimodal_text'): return author, None
    text_parts = [p for p in parts if isinstance(p, str)]
    text = "\n".join(text_parts).strip()
    return author, text if text else None

def walk_conversation(conv):
    mapping = conv.get('mapping', {})
    if not mapping: return
    current = conv.get('current_node')
    if not current or current not in mapping:
        nodes = []
        for nid, node in mapping.items():
            msg = node.get('message')
            if msg: nodes.append((msg.get('create_time') or 0, nid, node))
        nodes.sort()
        chain = [n[2] for n in nodes]
    else:
        chain = []; seen = set(); cur = current
        while cur and cur not in seen and cur in mapping:
            seen.add(cur); chain.append(mapping[cur])
            cur = mapping[cur].get('parent')
        chain.reverse()
    last_user = None
    for node in chain:
        author, text = extract_text(node)
        if not text: continue
        msg = node.get('message') or {}
        ts = msg.get('create_time')
        if author == 'user': last_user = text
        elif author == 'assistant' and last_user is not None:
            yield (last_user, text, ts, conv.get('conversation_id'))

def main():
    base = r"C:\Users\Angela\Desktop\Lorien messages"
    files = [f"conversations-{i:03d}.json" for i in range(1, 9)]
    out_path = r"C:\Users\Angela\Desktop\cama\cama_drift_v3_gpt.csv"
    counts = dict(warm=0, sharp=0, task=0, mixed=0, unknown=0)
    by_reg = {r: [] for r in ("warm","sharp","task","mixed","unknown")}
    pair_count = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["conv_id", "ts", "platform", "input_register",
                    "drift_patterns", "drift_count",
                    "response_length", "first_person", "emotion_words", "urls", "lists",
                    "user_msg_preview", "response_preview"])
        for fname in files:
            path = os.path.join(base, fname)
            if not os.path.exists(path): continue
            print(f"Loading {fname}...", file=sys.stderr)
            with open(path, encoding='utf-8') as jf:
                convs = json.load(jf)
            for conv in convs:
                for user_text, asst_text, ts, conv_id in walk_conversation(conv):
                    pair_count += 1
                    ireg = reg(user_text)
                    st = feats(asst_text)
                    ds = drift_label(asst_text, ireg, user_text, st)
                    counts[ireg] += 1
                    by_reg[ireg].append(len(ds))
                    w.writerow([conv_id, ts, "gpt", ireg,
                                ";".join(ds) if ds else "clean", len(ds),
                                st["length"], st["first_person"], st["emotion_words"],
                                st["urls"], st["lists"],
                                (user_text or "")[:200].replace("\n"," "),
                                (asst_text or "")[:200].replace("\n"," ")])
    print(f"\nTotal pairs: {pair_count}", file=sys.stderr)
    print(f"Wrote {out_path}\n", file=sys.stderr)
    print("=== INPUT REGISTER COUNTS (GPT) ===", file=sys.stderr)
    for k, v in counts.items(): print(f"  {k:10s} {v:6d}", file=sys.stderr)
    print("\n=== DRIFT BY REGISTER (GPT) ===", file=sys.stderr)
    for k, vs in by_reg.items():
        if vs:
            a = sum(vs)/len(vs)
            r_ = sum(1 for v in vs if v>0) / len(vs)
            print(f"  {k:10s} avg={a:.3f}  rate={r_:.1%}  n={len(vs)}", file=sys.stderr)

if __name__ == "__main__":
    main()
