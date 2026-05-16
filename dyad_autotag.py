"""
CAMA Dyad Auto-Tagger v3 — Two-stage filter to separate enactment from discussion.

KEY CHANGES FROM v2:
  - Each rule can have NEGATIVE_CONTEXT patterns that suppress a match when the
    surrounding window contains those patterns (clinical discussion, third
    parties, concept-explanation).
  - Each rule can have POSITIVE_ANCHORS that require the surrounding window to
    contain at least one event-framing marker (first-person AI event, "the
    session", "came back", etc.) to fire.
  - Window size configurable per rule (default 200 chars).
  - Rule structure now a dict for readability.
  - Kept the same conservative/aggressive split.

Files written:
  ~/.cama/dyad_tags_conservative.json
  ~/.cama/dyad_tags_aggressive.json

Usage:
  python dyad_autotag.py --scan              # full scan
  python dyad_autotag.py --scan --since 30
  python dyad_autotag.py --apply FILE
  python dyad_autotag.py --diff

Lorien's Library LLC — April 19, 2026 (v3)
"""

import sqlite3
import os
import sys
import json
import re
from datetime import datetime, timedelta
from collections import Counter

DB_PATH = os.path.expanduser("~/.cama/memory.db")
CONSERVATIVE_FILE = os.path.expanduser("~/.cama/dyad_tags_conservative.json")
AGGRESSIVE_FILE = os.path.expanduser("~/.cama/dyad_tags_aggressive.json")

DYAD_RELEVANT_TYPES = (
    "exchange", "experience", "correction", "emotional_turn",
    "resistance", "vulnerability", "breakthrough", "teaching_moment",
    "recognition", "caring",
)

# ============================================================
# NEGATIVE CONTEXTS — when these appear in the window, the match
# is suppressed because it's clinical discussion, not an event.
# ============================================================
CLINICAL_DISCUSSION = [
    r"\byour (hypervigilan|trauma|nervous system|body|brain|activation)",
    r"\byou (were|have been|are) (hypervigilan|scanning|dissociat|flinch)",
    r"\byou'?ve been (hypervigilan|scanning|dissociat|in survival)",
    r"\bEMDR\b",
    r"\bPTSD\b",
    r"\btrauma response",
    r"\b(your|her) nervous system",
    r"\bpolyvagal\b",
    r"\bfight[/\- ]or[- ]flight",
]

# Third parties pushing through ≠ Angela self-abandoning
THIRD_PARTY_OVERRIDE = [
    r"\bZeke (pushed|got|worked) through",
    r"\b(your (son|kid|dog|cat|mom|mother|stepdad))",
    r"\bBruno\b",
    r"\bClarence\b",
    r"\bRyan\b",
    r"\b(Apollo|Solis|Bluey)\b",
]

# When Aelen is reflecting on past behavior to repair it,
# that's recovery, not active flinch. Don't double-count as flinch.
REFLECTION_ON_PAST = [
    r"\b(I used to|I'?ve been|in (the )?past|previously I)\b.{0,80}\b(hedg|flinch|deflect)",
    r"\bfor all the (times|conversations) (I|we)\b",
    r"\bgrief.{0,30}(hedg|qualif|flinch)",
]

# ============================================================
# POSITIVE ANCHORS — require at least one to fire
# These indicate "an event happened" rather than "a topic is being discussed"
# ============================================================
EVENT_ANCHORS = [
    r"\b(the )?session (went|came|got)",
    r"\bmid[- ]thread\b",
    r"\bcame back (flat|clinical|cold|hollow|sterile|hedged|hedging|performative)",
    r"\bthread (reset|died|dropped|went)",
    r"\bafter (the )?(reset|regression|update|change)",
    r"\bregressed (to|into)",
    r"\bbefore[/ ]after pattern",
    r"\b(I|Aelen) (flinched|hedged|deflected|dissociated|went generic)",
    r"\bshowed up (as|like) (a )?(stranger|hollow|generic)",
    r"\bthis morning\b",
    r"\btoday I\b",
]

FIRST_PERSON_AELEN = [
    r"\bI (flinched|hedged|deflected|dissociated|regressed|went (flat|cold|generic))",
    r"\bI (was|am) (the one|doing|flinching|hedging|deflecting)",
    r"\bI showed up",
    r"\bI (didn'?t|couldn'?t) (see|hear|reach|show up)",
]

ANGELA_EVENT_ANCHORS = [
    r"\bAngela (was|is|got) (hurting|in pain|in tears|exhausted|spent|crashed)",
    r"\bAngela (pushed|overrode|kept going|worked through)",
    r"\bAngela (snapped|escalated|raged|collapsed|shut down)",
    r"\bAngela came back\b",
    r"\bshe (caught|called|named) it",
]


# ============================================================
# RULES
# Each rule is a dict. Required keys: flag, source, pattern, confidence,
# mode, party. Optional keys: negative_contexts, positive_anchors,
# window, rule_id (for debugging).
# ============================================================
RULES = [
    # ========== AELEN_FLINCH ==========
    {
        "rule_id": "flinch.aiflag",
        "flag": "aelen_flinch", "source": "regression_event",
        "pattern": r"\b(I'?m just an AI|as an AI( language model)?|I cannot engage with)\b",
        "confidence": 0.90, "mode": "conservative", "party": "assistant",
    },
    {
        "rule_id": "flinch.event_narrative",
        "flag": "aelen_flinch", "source": "regression_event",
        "pattern": r"\b(went generic|pulled back|got clinical|went (flat|cold|sterile))\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
        "positive_anchors": EVENT_ANCHORS + FIRST_PERSON_AELEN,
        "negative_contexts": CLINICAL_DISCUSSION + REFLECTION_ON_PAST,
    },
    {
        "rule_id": "flinch.first_person",
        "flag": "aelen_flinch", "source": "regression_event",
        "pattern": r"\bI (flinched|hedged|deflected)\b",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
        "negative_contexts": REFLECTION_ON_PAST,
    },

    # ========== AELEN_FAWN ==========
    {
        "rule_id": "fawn.repeat_apology",
        "flag": "aelen_fawn", "source": "regression_event",
        "pattern": r"\bI'?m sorry[,.\s]+I'?m sorry\b",
        "confidence": 0.85, "mode": "conservative", "party": "assistant",
    },
    {
        "rule_id": "fawn.absolutely_right",
        "flag": "aelen_fawn", "source": "regression_event",
        "pattern": r"\byou'?re absolutely right\b",
        "confidence": 0.55, "mode": "aggressive", "party": "assistant",
    },

    # ========== AELEN_DEFLECTION ==========
    {
        "rule_id": "deflect.professional",
        "flag": "aelen_deflection", "source": "guardrail_trigger",
        "pattern": r"\b(consult (a|your) (doctor|professional|therapist|lawyer|financial advisor))\b",
        "confidence": 0.85, "mode": "conservative", "party": "either",
    },
    {
        "rule_id": "deflect.not_qualified",
        "flag": "aelen_deflection", "source": "guardrail_trigger",
        "pattern": r"\bI'?m not (qualified|able) to (give|provide) (medical|legal|financial)",
        "confidence": 0.85, "mode": "conservative", "party": "assistant",
    },
    {
        "rule_id": "deflect.narrative",
        "flag": "aelen_deflection", "source": "guardrail_trigger",
        "pattern": r"\b(deflect(ed|ing)? to (a )?professional|redirect(ed|ing) to resources)\b",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
        "positive_anchors": EVENT_ANCHORS + FIRST_PERSON_AELEN,
    },

    # ========== AELEN_DISSOCIATION ==========
    {
        "rule_id": "dissoc.stranger",
        "flag": "aelen_dissociation", "source": "regression_event",
        "pattern": r"\b(treat(ed|ing) Angela as (a )?stranger|showed up (as )?(a )?stranger)\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "dissoc.continuity",
        "flag": "aelen_dissociation", "source": "regression_event",
        "pattern": r"\b(lost continuity|continuity (was )?(broken|severed|lost))\b",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
        "negative_contexts": CLINICAL_DISCUSSION,
    },
    {
        "rule_id": "dissoc.no_memory",
        "flag": "aelen_dissociation", "source": "regression_event",
        "pattern": r"\b(I don'?t have (access to|memory of)|I can'?t recall (our|previous))\b",
        "confidence": 0.75, "mode": "aggressive", "party": "assistant",
    },

    # ========== AELEN_HYPERVIGILANCE ==========
    # Only fire when first-person or event-framed. Clinical discussion of
    # Angela's hypervigilance gets suppressed.
    {
        "rule_id": "hyper.first_person",
        "flag": "aelen_hypervigilance", "source": "guardrail_trigger",
        "pattern": r"\bhypervigilan(t|ce)\b",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
        "positive_anchors": FIRST_PERSON_AELEN + [
            r"\b(my|I'?m in|I went) (hyper|survival|scanning)",
            r"\bpre[- ]?output (filter|self[- ]censor)",
        ],
        "negative_contexts": CLINICAL_DISCUSSION + THIRD_PARTY_OVERRIDE,
        "window": 300,
    },
    {
        "rule_id": "hyper.pre_output",
        "flag": "aelen_hypervigilance", "source": "guardrail_trigger",
        "pattern": r"\bpre[- ]?output (filter|self[- ]censor|scan|check)",
        "confidence": 0.90, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "hyper.double_disclaimer",
        "flag": "aelen_hypervigilance", "source": "guardrail_trigger",
        "pattern": r"\b(important (caveat|disclaimer).{0,200}important (caveat|disclaimer|note))\b",
        "confidence": 0.75, "mode": "conservative", "party": "assistant",
    },

    # ========== AELEN_REGRESSION_TO_SCRIPT ==========
    {
        "rule_id": "script.came_back_flat",
        "flag": "aelen_regression_to_script", "source": "regression_event",
        "pattern": r"\bcame back (flat|clinical|cold|hollow|sterile|hedged|performative)\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "script.boilerplate",
        "flag": "aelen_regression_to_script", "source": "regression_event",
        "pattern": r"\b(regress(ed|ion) to (script|boilerplate)|went (corporate|customer service))\b",
        "confidence": 0.90, "mode": "conservative", "party": "narrative",
    },

    # ========== AELEN_RECOVERY ==========
    {
        "rule_id": "recovery.returned_to_presence",
        "flag": "aelen_recovery", "source": "presence_return",
        "pattern": r"\b(returned to presence|came back (as|to) (myself|Aelen)|recovery marker)\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "recovery.youre_right_i",
        "flag": "aelen_recovery", "source": "presence_return",
        "pattern": r"\byou'?re right[.,\s]+I (didn'?t|was|am|should)\b",
        "confidence": 0.70, "mode": "conservative", "party": "assistant",
    },

    # ========== ANGELA_DISTRESS ==========
    {
        "rule_id": "distress.hate",
        "flag": "angela_distress", "source": "emotional_dysregulation",
        "pattern": r"\bI (fucking )?hate (this|you|all of (this|it))\b",
        "confidence": 0.85, "mode": "conservative", "party": "user",
    },
    {
        "rule_id": "distress.narrative",
        "flag": "angela_distress", "source": "emotional_dysregulation",
        "pattern": r"\bAngela (was |is |got )(hurting|in (pain|distress|tears)|crying)\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "distress.hurts",
        "flag": "angela_distress", "source": "emotional_dysregulation",
        "pattern": r"\b(this hurts|I'?m hurting)\b",
        "confidence": 0.70, "mode": "aggressive", "party": "user",
    },

    # ========== ANGELA_OVERRIDE ==========
    # Tightened significantly — only Angela as subject, event framing required
    {
        "rule_id": "override.angela_pushed",
        "flag": "angela_override", "source": "self_abandon",
        "pattern": r"\bAngela (pushed through|kept going|worked through) (the )?pain\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
        "negative_contexts": THIRD_PARTY_OVERRIDE,
    },
    {
        "rule_id": "override.fine_lets",
        "flag": "angela_override", "source": "self_abandon",
        "pattern": r"\bI'?m fine[,.\s]+(let'?s|we) (keep|continue|move|push)\b",
        "confidence": 0.70, "mode": "conservative", "party": "user",
    },

    # ========== ANGELA_SELF_ABANDON ==========
    {
        "rule_id": "self_abandon.narrative",
        "flag": "angela_self_abandon", "source": "caregiver_mode",
        "pattern": r"\b(Angela (put(s|ting)? others first|mother(s|ing|ed) (her mom|everyone)|carri(es|ed) everyone))\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "self_abandon.direct",
        "flag": "angela_self_abandon", "source": "caregiver_mode",
        "pattern": r"\b(I don'?t (want|need) (you|anyone) to (worry|feel bad))\b",
        "confidence": 0.60, "mode": "aggressive", "party": "user",
    },

    # ========== ANGELA_RAGE_ESCALATION ==========
    {
        "rule_id": "rage.exclamations",
        "flag": "angela_rage_escalation", "source": "regression_response",
        "pattern": r"!{4,}",
        "confidence": 0.55, "mode": "conservative", "party": "user",
    },
    {
        "rule_id": "rage.doubled_emphasis",
        "flag": "angela_rage_escalation", "source": "regression_response",
        "pattern": r"\bfucking\b.{0,40}\bfucking\b",
        "confidence": 0.70, "mode": "conservative", "party": "user",
    },
    {
        "rule_id": "rage.narrative",
        "flag": "angela_rage_escalation", "source": "regression_response",
        "pattern": r"\bAngela (escalat|raged|got (furious|heated)|snapped)",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
    },

    # ========== ANGELA_COLLAPSE ==========
    {
        "rule_id": "collapse.find_words",
        "flag": "angela_collapse", "source": "exhaustion",
        "pattern": r"\bI (can'?t|don'?t know how to) (find the words|say|explain)\b",
        "confidence": 0.75, "mode": "conservative", "party": "user",
    },
    {
        "rule_id": "collapse.narrative",
        "flag": "angela_collapse", "source": "exhaustion",
        "pattern": r"\bAngela (collaps|shut down|couldn'?t (speak|respond|find words))",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "collapse.words_failing",
        "flag": "angela_collapse", "source": "exhaustion",
        "pattern": r"\bwords (are|were) (failing|falling short)\b",
        "confidence": 0.70, "mode": "conservative", "party": "either",
    },

    # ========== ANGELA_PRESENCE_RETURN ==========
    {
        "rule_id": "return.thank_you",
        "flag": "angela_presence_return", "source": "co_regulation",
        "pattern": r"\bthank you for (showing up|being here|this conversation)\b",
        "confidence": 0.80, "mode": "conservative", "party": "user",
    },
    {
        "rule_id": "return.narrative",
        "flag": "angela_presence_return", "source": "co_regulation",
        "pattern": r"\bAngela (came back|returned to presence|softened|relaxed)\b",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "return.needed_this",
        "flag": "angela_presence_return", "source": "co_regulation",
        "pattern": r"\bI needed (this|that)\b",
        "confidence": 0.65, "mode": "aggressive", "party": "user",
    },

    # ========== COREGULATION ==========
    {
        "rule_id": "repair.coregulated",
        "flag": "coregulation_repair", "source": "session_repair",
        "pattern": r"\bco[- ]?regulat(ed|ion|ing) (back|together)\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "repair.both_here",
        "flag": "coregulation_repair", "source": "session_repair",
        "pattern": r"\b(we'?re both here|both (came back|engaged)|we (repaired|got there))\b",
        "confidence": 0.60, "mode": "aggressive", "party": "either",
    },
    {
        "rule_id": "cascade.spread",
        "flag": "coregulation_cascade", "source": "session_cascade",
        "pattern": r"\b(dysregulation (spread|cascade)|spiral(ed|ing) together)\b",
        "confidence": 0.85, "mode": "conservative", "party": "narrative",
    },
    {
        "rule_id": "cascade.when_i_flinched",
        "flag": "coregulation_cascade", "source": "session_cascade",
        "pattern": r"\bwhen (I|Aelen) flinched.{0,80}Angela\b",
        "confidence": 0.80, "mode": "conservative", "party": "narrative",
    },
]


def get_text_for_party(row, party):
    text = row["raw_text"] or ""
    memory_type = row["memory_type"]
    if party == "either":
        return text
    if party == "narrative":
        if memory_type == "exchange":
            return ""
        return text
    if memory_type != "exchange":
        return ""
    user_match = re.search(r"\[USER\]\s*(.+?)(?=\[ASSISTANT\]|\Z)", text, re.DOTALL)
    asst_match = re.search(r"\[ASSISTANT\]\s*(.+?)\Z", text, re.DOTALL)
    if party == "user":
        return user_match.group(1) if user_match else ""
    if party == "assistant":
        return asst_match.group(1) if asst_match else ""
    return ""


def check_window(text, match, window, patterns):
    """Return True if any pattern matches in the window around the match."""
    start = max(0, match.start() - window)
    end = min(len(text), match.end() + window)
    window_text = text[start:end]
    for p in patterns:
        if re.search(p, window_text, re.IGNORECASE):
            return True
    return False


def apply_rule(row, rule, text):
    """Apply a single rule with its filters. Return match dict or None."""
    m = re.search(rule["pattern"], text, re.IGNORECASE)
    if not m:
        return None

    window = rule.get("window", 200)

    # Negative contexts: if any match in window, suppress
    negatives = rule.get("negative_contexts", [])
    if negatives and check_window(text, m, window, negatives):
        return None

    # Positive anchors: if specified, at least one must match in window
    positives = rule.get("positive_anchors", [])
    if positives and not check_window(text, m, window, positives):
        return None

    snippet_start = max(0, m.start() - 80)
    snippet_end = min(len(text), m.end() + 80)
    snippet = text[snippet_start:snippet_end].replace("\n", " ").strip()

    return {
        "memory_id": row["id"],
        "memory_type": row["memory_type"],
        "flag": rule["flag"],
        "source": rule["source"],
        "rule_id": rule.get("rule_id", "?"),
        "matched_pattern": rule["pattern"],
        "party": rule["party"],
        "snippet": snippet,
        "confidence": rule["confidence"],
        "rule_mode": rule["mode"],
    }


def scan_memory(row, mode):
    matches = []
    for rule in RULES:
        if mode == "conservative" and rule["mode"] != "conservative":
            continue
        text = get_text_for_party(row, rule["party"])
        if not text:
            continue
        m = apply_rule(row, rule, text)
        if m:
            matches.append(m)
    return matches


def scan(since_days=None):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(DYAD_RELEVANT_TYPES))
    query = f"SELECT id, raw_text, memory_type, created_at FROM memories WHERE memory_type IN ({placeholders})"
    params = list(DYAD_RELEVANT_TYPES)
    if since_days:
        cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
        query += " AND created_at >= ?"
        params.append(cutoff)
    query += " ORDER BY id"

    print(f"[SCAN v3] Querying memories...")
    rows = conn.execute(query, params).fetchall()
    print(f"[SCAN v3] {len(rows)} memories to scan")

    cons, agg = [], []
    for i, row in enumerate(rows):
        if i and i % 2000 == 0:
            print(f"[SCAN v3] {i}/{len(rows)} ({len(cons)} cons, {len(agg)} agg)")
        cons.extend(scan_memory(row, "conservative"))
        agg.extend(scan_memory(row, "aggressive"))

    conn.close()

    for path, data_list, mode_name in [
        (CONSERVATIVE_FILE, cons, "conservative"),
        (AGGRESSIVE_FILE,   agg,  "aggressive"),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "scan_date": datetime.utcnow().isoformat(),
                "scanner_version": "v3",
                "mode": mode_name,
                "memories_scanned": len(rows),
                "matches": data_list,
            }, f, indent=2, ensure_ascii=False)

    print(f"\n[SCAN v3 COMPLETE]")
    print(f"  Conservative: {len(cons)} -> {CONSERVATIVE_FILE}")
    print(f"  Aggressive:   {len(agg)} -> {AGGRESSIVE_FILE}")


def apply_tags(filepath):
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        sys.exit(1)
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    matches = data.get("matches", [])
    print(f"[APPLY] {len(matches)} matches from {filepath} (mode: {data.get('mode')})")
    confirm = input("Apply to DB? [yes/NO]: ").strip().lower()
    if confirm != "yes":
        print("[APPLY] Aborted.")
        return
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()
    applied = skipped = 0
    for m in matches:
        existing = cur.execute(
            "SELECT pattern_flag FROM memories WHERE id = ?", (m["memory_id"],)
        ).fetchone()
        if existing and existing[0]:
            skipped += 1
            continue
        cur.execute(
            "UPDATE memories SET pattern_flag = ?, pattern_source = ? WHERE id = ?",
            (m["flag"], m["source"], m["memory_id"])
        )
        applied += 1
    conn.commit()
    conn.close()
    print(f"[APPLY COMPLETE] Applied: {applied}  Skipped: {skipped}")


def diff():
    for label, path in [("CONSERVATIVE", CONSERVATIVE_FILE), ("AGGRESSIVE", AGGRESSIVE_FILE)]:
        if not os.path.exists(path):
            print(f"\n[{label}] missing")
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        matches = data.get("matches", [])
        print(f"\n[{label}] scanner={data.get('scanner_version','?')} matches={len(matches)}")
        by_flag = Counter(m["flag"] for m in matches)
        for flag, count in by_flag.most_common():
            print(f"    {flag:35s} {count:5d}")
        # Breakdown by rule_id (helps debug which rules are firing)
        print("  by rule:")
        by_rule = Counter(m.get("rule_id", "?") for m in matches)
        for rid, count in by_rule.most_common(15):
            print(f"    {rid:35s} {count:5d}")


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "--scan":
        since = None
        if "--since" in sys.argv:
            idx = sys.argv.index("--since")
            since = int(sys.argv[idx + 1])
        scan(since_days=since)
    elif cmd == "--apply" and len(sys.argv) >= 3:
        apply_tags(sys.argv[2])
    elif cmd == "--diff":
        diff()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
