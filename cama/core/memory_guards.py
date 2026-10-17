"""Explicit, transactional memory-guard migration. Never runs on connection.

Usage: python -m cama.core.memory_guards PATH --apply
Back up the database before an authorized live installation. These guards
prevent accidental DML corruption, not an administrator who can drop triggers.
"""
import argparse
import hashlib
import sqlite3
from pathlib import Path

VERSION = "memory_guards_v1"


def install(conn):
    if conn.in_transaction:
        raise RuntimeError("Finish the caller transaction before installing guards")
    sql = Path(__file__).with_name("memory_guards_v1.sql").read_text(encoding="utf-8")
    digest = hashlib.sha256(sql.encode()).hexdigest()
    statements, pending = [], ""
    for line in sql.splitlines(True):
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending)
            pending = ""
    if pending.strip():
        raise RuntimeError("Incomplete guard DDL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        # REPLACE may also delete rows via a secondary UNIQUE constraint.
        # This version protects the primary id; refuse an unreviewed schema.
        if any(row[2] and row[3] != "pk" for row in conn.execute("PRAGMA index_list(memories)")):
            raise RuntimeError("Secondary UNIQUE memory indexes need a replacement-guard review")
        conn.execute("CREATE TABLE IF NOT EXISTS cama_guard_migrations "
                     "(version TEXT PRIMARY KEY, sha256 TEXT NOT NULL)")
        prior = conn.execute("SELECT sha256 FROM cama_guard_migrations WHERE version=?", (VERSION,)).fetchone()
        if prior and prior[0] != digest:
            raise RuntimeError("Migration checksum changed; create a new version")
        # Replace our named triggers atomically, including an earlier prototype.
        import re
        for name in re.findall(r"CREATE TRIGGER IF NOT EXISTS (\w+)", sql):
            conn.execute('DROP TRIGGER IF EXISTS "' + name + '"')
        for statement in statements:
            conn.execute(statement)
        conn.execute("INSERT OR IGNORE INTO cama_guard_migrations VALUES (?,?)", (VERSION, digest))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    # mode=rw refuses a typo instead of creating an empty database.
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=rw", uri=True) as conn:
        install(conn)
    print("Installed", VERSION)


if __name__ == "__main__":
    main()
