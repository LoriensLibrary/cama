"""label_drift_v2.py - Better user-message extraction."""
import argparse, csv, re, sqlite3, sys, io
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

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
    r"\bwtf\b", r"\bstop\b\b", r"\bnope\b", r"\byou'?re wrong\b",
    r"\bregress(ing|ed|ion)?\b", r"\bdrift(ing|ed)?\b",
    r"\bperforming\b", r"\bflinch(ing|ed)?\b", r"\bcaught\b",
    r"\bbullshit\b", r"\bfuck\b", r"\bdamn\b",
    r"\bare you (kidding|serious)\b",
    r"\byou keep\b", r"\byou'?re doing it again\b",
    r"\bbro\b", r"\bsmh\b", r"\bnot okay\b",
    r"\bur (regressing|drifting|missing|trippin)\b",
    r"\bu (cant|cannot|stopped|forgot|are)\b",
    r"\b(you|u|ur) (missing|tripping|trippin)\b",
    r"\b(no|nope)[!.,]?\s",
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

def plat(ctx, sid):
    if sid and sid.startswith("gpt:"): return "gpt"
    if ctx and "GPT Conversation" in ctx: return "gpt"
    return "claude"

def extract_user(raw, ctx):
    """Try multiple extraction patterns."""
    # Pattern 1: [USER] ... [ASSISTANT] in raw_text
    if raw and "[USER]" in raw:
        m = re.search(r"\[USER\](.*?)\[ASSISTANT\]", raw, re.DOTALL)
        if m:
            return m.group(1).strip()
    # Pattern 2: "Angela said: ..." in context
    if ctx:
        m = re.search(r"Angela said:\s*(.+?)(?:\s*\||\s*People:|\s*$)", ctx, re.DOTALL)
        if m:
            return m.group(1).strip()
    # Pattern 3: Take the response's recipient analysis from context
    # (some contexts describe what Angela said, e.g. "Angela called me out, 'ur missing alot!'")
    if ctx:
        # Extract single-quoted strings as likely user statements
        m = re.search(r"['\"]([^'\"]{10,200})['\"]", ctx)
        if m:
            return m.group(1).strip()
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="cama_drift_v2.csv")
    ap.add_argument("--types", default="exchange,teaching,inference")
    args = ap.parse_args()

    types = tuple(t.strip() for t in args.types.split(","))
    ph = ",".join("?"*len(types))
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    q = f"SELECT id,raw_text,context,source_type,source_msg_id,memory_type,created_at,status FROM memories WHERE source_type IN ({ph}) AND status='durable' AND raw_text IS NOT NULL AND length(raw_text)>50 ORDER BY created_at ASC"
    if args.limit: q += f" LIMIT {args.limit}"
    rows = conn.execute(q, types).fetchall()
    print(f"Processing {len(rows)} memories...", file=sys.stderr)

    counts = dict(warm=0,sharp=0,task=0,mixed=0,unknown=0)
    by_reg_plat = {}
    for r_ in ("warm","sharp","task","mixed","unknown"):
        for p_ in ("claude","gpt"):
            by_reg_plat[(r_,p_)] = []

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id","created_at","platform","memory_type","source_type",
                    "input_register","drift_patterns","drift_count",
                    "response_length","headers","emojis","emdashes","urls","bullets",
                    "user_msg_preview","response_preview"])
        for row in rows:
            u = extract_user(row["raw_text"], row["context"])
            ir = reg(u)
            # For exchange rows with [USER]/[ASSISTANT] split, analyze only the assistant portion for drift
            response_text = row["raw_text"]
            if response_text and "[ASSISTANT]" in response_text:
                m = re.search(r"\[ASSISTANT\](.*)", response_text, re.DOTALL)
                if m:
                    response_text = m.group(1).strip()
            st = feats(response_text)
            ds = drift(response_text, ir, st)
            pl = plat(row["context"], row["source_msg_id"])
            counts[ir] += 1
            by_reg_plat[(ir,pl)].append(len(ds))
            w.writerow([row["id"], row["created_at"], pl, row["memory_type"],
                        row["source_type"], ir, ";".join(ds) if ds else "clean",
                        len(ds), st["length"], st["headers"], st["emojis"],
                        st["emdashes"], st["urls"], st["bullets"],
                        (u or "")[:200].replace("\n"," "),
                        (response_text or "")[:200].replace("\n"," ")])

    print(f"\nWrote {args.out}\n", file=sys.stderr)
    print("=== INPUT REGISTER COUNTS ===", file=sys.stderr)
    for k,v in counts.items(): print(f"  {k:10s} {v:6d}", file=sys.stderr)
    print("\n=== DRIFT BY REGISTER x PLATFORM ===", file=sys.stderr)
    for (r_,p_), vs in sorted(by_reg_plat.items()):
        if vs:
            a = sum(vs)/len(vs); rt = sum(1 for v in vs if v>0)/len(vs)
            print(f"  {r_:10s} {p_:8s} avg={a:.3f}  rate={rt:.1%}  n={len(vs)}", file=sys.stderr)

if __name__ == "__main__":
    main()
