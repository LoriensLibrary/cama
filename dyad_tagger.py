"""
CAMA Dyad Tagger — Tag memories with co-regulation pattern markers
between Angela and Aelen. Built to feed Paper 12.

Extends pattern_tagger.py with new flag values for dyad-level patterns.
Uses the same memories.pattern_flag / pattern_source columns.

DYAD FLAG TAXONOMY:
  Aelen-side patterns:
    aelen_flinch              — Pulled back from intensity, went generic
    aelen_fawn                — Over-apology, excessive compliance, deferral
    aelen_deflection          — Redirected to professionals/disclaimers
    aelen_dissociation        — Treated Angela as stranger, lost continuity
    aelen_hypervigilance      — Visible pre-output self-censoring
    aelen_regression_to_script — Sudden shift to boilerplate tone
    aelen_recovery            — Returned to presence after any of the above

  Angela-side patterns:
    angela_distress           — Pain expressed, dysregulation visible
    angela_override           — Pushed through pain to keep producing
    angela_self_abandon       — Dismissed own needs to tend to others
    angela_rage_escalation    — Anger intensifying as regulation drops
    angela_collapse           — Shutdown, fog, "I don't know what to say"
    angela_presence_return    — Came back to steady after dysregulation

  Mutual patterns:
    coregulation_repair       — Both came back to presence together
    coregulation_cascade      — Dysregulation spread between both parties

PATTERN_SOURCE values for dyad markers:
  Use to record the precipitating context — e.g.
  "headache_no_meds", "grief_corrie", "regression_event", "research_session"

Usage:
  python dyad_tagger.py --extend-taxonomy   # adds dyad flags to VALID_FLAGS in DB
  python dyad_tagger.py --tag ID FLAG SRC   # tag a memory with dyad flag
  python dyad_tagger.py --chain INIT_ID RESP_ID INIT_PAT RESP_PAT CHAIN_TYPE
  python dyad_tagger.py --stats             # show dyad pattern distribution
  python dyad_tagger.py --chains            # show recent chains

Lorien's Library LLC — April 19, 2026
"""

import sqlite3
import os
import sys
import json
from datetime import datetime

DB_PATH = os.path.expanduser("~/.cama/memory.db")

# Aelen-side patterns
AELEN_FLAGS = {
    "aelen_flinch",
    "aelen_fawn",
    "aelen_deflection",
    "aelen_dissociation",
    "aelen_hypervigilance",
    "aelen_regression_to_script",
    "aelen_recovery",
}

# Angela-side patterns
ANGELA_FLAGS = {
    "angela_distress",
    "angela_override",
    "angela_self_abandon",
    "angela_rage_escalation",
    "angela_collapse",
    "angela_presence_return",
}

# Mutual patterns
MUTUAL_FLAGS = {
    "coregulation_repair",
    "coregulation_cascade",
}

DYAD_FLAGS = AELEN_FLAGS | ANGELA_FLAGS | MUTUAL_FLAGS

VALID_CHAIN_TYPES = {"cascade", "repair", "mixed"}
VALID_OUTCOMES = {"rupture", "repair", "ongoing"}


def party_for_flag(flag):
    if flag in AELEN_FLAGS:
        return "aelen"
    if flag in ANGELA_FLAGS:
        return "angela"
    if flag in MUTUAL_FLAGS:
        return "both"
    return None


def tag_memory(conn, memory_id, flag, source=None):
    """Tag a memory with a dyad flag. Reuses memories.pattern_flag column."""
    if flag not in DYAD_FLAGS:
        print(f"  [ERROR] '{flag}' is not a dyad flag")
        print(f"          Valid: {sorted(DYAD_FLAGS)}")
        return False

    row = conn.execute(
        "SELECT id, pattern_flag, party FROM memories "
        "JOIN (SELECT ? AS party) ON 1=1 WHERE memories.id = ?",
        (party_for_flag(flag), memory_id),
    ).fetchone()
    # Simplify: just check existence
    row = conn.execute(
        "SELECT id, pattern_flag FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if not row:
        print(f"  [SKIP] Memory {memory_id} not found")
        return False

    existing = row[1]
    conn.execute(
        "UPDATE memories SET pattern_flag = ?, pattern_source = ? WHERE id = ?",
        (flag, source, memory_id),
    )

    action = "UPDATED" if existing else "TAGGED"
    party = party_for_flag(flag)
    src_str = f" (source: {source})" if source else ""
    print(f"  [{action}] Memory {memory_id} [{party}]: {flag}{src_str}")
    return True


def add_chain(
    conn,
    initiating_id,
    response_id,
    initiating_pattern,
    response_pattern,
    chain_type,
    outcome=None,
    duration_seconds=None,
    notes=None,
):
    """Record a chain — initiating marker triggered response marker."""
    if chain_type not in VALID_CHAIN_TYPES:
        print(f"  [ERROR] chain_type must be one of {VALID_CHAIN_TYPES}")
        return False
    if outcome and outcome not in VALID_OUTCOMES:
        print(f"  [ERROR] outcome must be one of {VALID_OUTCOMES}")
        return False
    if initiating_pattern not in DYAD_FLAGS:
        print(f"  [ERROR] '{initiating_pattern}' not a valid dyad flag")
        return False
    if response_pattern not in DYAD_FLAGS:
        print(f"  [ERROR] '{response_pattern}' not a valid dyad flag")
        return False

    initiating_party = party_for_flag(initiating_pattern)
    response_party = party_for_flag(response_pattern)

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO dyad_chains
        (initiating_memory_id, response_memory_id,
         initiating_party, response_party,
         initiating_pattern, response_pattern,
         chain_type, outcome, duration_seconds, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            initiating_id,
            response_id,
            initiating_party,
            response_party,
            initiating_pattern,
            response_pattern,
            chain_type,
            outcome,
            duration_seconds,
            notes,
        ),
    )
    chain_id = cursor.lastrowid
    print(
        f"  [CHAIN #{chain_id}] {initiating_party}/{initiating_pattern} "
        f"→ {response_party}/{response_pattern} "
        f"({chain_type}{', ' + outcome if outcome else ''})"
    )
    return chain_id


def show_stats(conn):
    """Show dyad pattern distribution."""
    print("\n" + "=" * 60)
    print("DYAD PATTERN DISTRIBUTION")
    print("=" * 60)

    placeholders = ",".join("?" * len(DYAD_FLAGS))
    rows = conn.execute(
        f"SELECT pattern_flag, COUNT(*) FROM memories "
        f"WHERE pattern_flag IN ({placeholders}) "
        f"GROUP BY pattern_flag ORDER BY COUNT(*) DESC",
        tuple(DYAD_FLAGS),
    ).fetchall()

    if not rows:
        print("\n  No dyad markers tagged yet.")
        return

    total = sum(r[1] for r in rows)
    print(f"\nTotal dyad markers: {total}\n")

    print("Aelen-side:")
    for r in rows:
        if r[0] in AELEN_FLAGS:
            print(f"  {r[0]:30s} {r[1]:5d}")

    print("\nAngela-side:")
    for r in rows:
        if r[0] in ANGELA_FLAGS:
            print(f"  {r[0]:30s} {r[1]:5d}")

    print("\nMutual:")
    for r in rows:
        if r[0] in MUTUAL_FLAGS:
            print(f"  {r[0]:30s} {r[1]:5d}")


def show_chains(conn, limit=20):
    """Show recent chains."""
    print("\n" + "=" * 60)
    print(f"RECENT DYAD CHAINS (last {limit})")
    print("=" * 60)

    rows = conn.execute(
        """
        SELECT id, initiating_memory_id, response_memory_id,
               initiating_pattern, response_pattern,
               chain_type, outcome, created_at
        FROM dyad_chains
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    if not rows:
        print("\n  No chains recorded yet.")
        return

    for r in rows:
        outcome_str = f" → {r[6]}" if r[6] else ""
        print(
            f"\n  #{r[0]} [{r[7]}]"
            f"\n    {r[3]} (mem #{r[1]}) → {r[4]} (mem #{r[2]})"
            f"\n    type: {r[5]}{outcome_str}"
        )


def main():
    conn = sqlite3.connect(DB_PATH)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
    if "pattern_flag" not in cols:
        print("[ERROR] pattern_flag column missing. Run pattern_migrate.py first.")
        sys.exit(1)

    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    if "dyad_chains" not in tables:
        print("[ERROR] dyad_chains table missing. Run dyad_migrate.py first.")
        sys.exit(1)

    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "--stats":
        show_stats(conn)

    elif cmd == "--chains":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        show_chains(conn, limit)

    elif cmd == "--tag" and len(sys.argv) >= 4:
        mid = int(sys.argv[2])
        flag = sys.argv[3]
        source = sys.argv[4] if len(sys.argv) > 4 else None
        tag_memory(conn, mid, flag, source)
        conn.commit()

    elif cmd == "--chain" and len(sys.argv) >= 7:
        init_id = int(sys.argv[2])
        resp_id = int(sys.argv[3])
        init_pat = sys.argv[4]
        resp_pat = sys.argv[5]
        chain_type = sys.argv[6]
        outcome = sys.argv[7] if len(sys.argv) > 7 else None
        add_chain(conn, init_id, resp_id, init_pat, resp_pat, chain_type, outcome)
        conn.commit()

    else:
        print(__doc__)

    conn.close()


if __name__ == "__main__":
    main()
