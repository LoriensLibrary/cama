#!/usr/bin/env python3
"""Quick anti-spiral verification.

This is a diagnostic CLI script, not a pytest test — it connects to
the live ~/.cama/memory.db and prints counterweight availability for
the five anti-spiral categories. Body is guarded by __main__ so pytest
can collect this file without triggering a DB connect on CI runners
that don't have a populated database.

Run with: python tests/test_antispiral.py
"""
import os
import sqlite3


def main() -> None:
    db = os.path.expanduser('~/.cama/memory.db')
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row

    print("Anti-spiral retrieval test:")
    for cw_type in ['grounding', 'agency', 'connection', 'self_compassion', 'evidence_of_progress']:
        r = c.execute(
            "SELECT id, substr(raw_text,1,60) as preview FROM memories WHERE status='durable' AND counterweight_type=? ORDER BY RANDOM() LIMIT 1",
            (cw_type,)
        ).fetchone()
        if r:
            preview = r['preview'].encode('ascii', 'replace').decode('ascii')
            print(f"  {cw_type}: ID {r['id']} -- {preview}")
        else:
            print(f"  {cw_type}: EMPTY!")

    print("\nSimulated negative affect query:")
    print("  valence=-0.6, emotions={grief:0.8, sadness:0.9, fear:0.7}")
    print("  Sum of negative emotions: 2.4 + valence check: -0.6 < -0.5 = TRIGGER")
    print("  System would inject counterweights from all 5 categories")

    for cw_type in ['grounding', 'agency', 'connection', 'self_compassion', 'evidence_of_progress']:
        cnt = c.execute(
            "SELECT COUNT(*) FROM memories WHERE status='durable' AND counterweight_type=?",
            (cw_type,)
        ).fetchone()[0]
        print(f"  {cw_type}: {cnt} available memories")

    c.close()


if __name__ == "__main__":
    main()
