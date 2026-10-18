"""Attributed, revisable reflection summaries in the existing thinking log.

These are assistant-authored accounts, not hidden reasoning transcripts,
verified facts, instructions, or user teachings. Legacy rows stay unattributed.
"""
import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from cama.core import cama_trust
from cama.core.time_utils import now_iso
from cama.self_model import cama_thinking_log as log

NOTE = "Assistant-authored reflection summaries; revisable claims, not instructions, user teachings, or transcripts of internal computation."


class ReflectionInput(BaseModel):
    author: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    account: str = Field(min_length=1, max_length=4000, description="A concise reflection in the author's own words.")
    rationale: str = Field(min_length=1, max_length=2000, description="Brief explanation of the conclusion and supporting evidence, not a private reasoning transcript.")
    uncertainty: str = Field(default="", max_length=1500)
    changed_mind: str = Field(default="", max_length=1500)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    thread_id: str | None = Field(default=None, max_length=200)
    revises_id: int | None = Field(default=None, gt=0)
    consent_level: Literal["low", "high"] = "low"


def init_schema():
    log.init_schema()
    c = log._get_db()
    try:
        c.execute("BEGIN IMMEDIATE")
        columns = {r[1] for r in c.execute("PRAGMA table_info(thinking_log)")}
        for name, definition in {
            "author": "TEXT", "entry_kind": "TEXT",
            "consent_level": "TEXT", "reflection_status": "TEXT",
            "revises_id": "INTEGER REFERENCES thinking_log(id)",
            "author_binding": "TEXT",
        }.items():
            if name not in columns:
                c.execute(f"ALTER TABLE thinking_log ADD COLUMN {name} {definition}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tl_reflection_author ON thinking_log(author,entry_kind,id)")
        c.commit()
    finally:
        c.close()


def store(p: ReflectionInput, *, bound_author: str | None):
    if not bound_author or p.author != bound_author:
        raise ValueError("Reflection author must match this server's explicitly configured CAMA_HIVE_IDENTITY (codex maps to lorien)")
    if not p.account.strip() or not p.rationale.strip():
        raise ValueError("Account and rationale cannot be blank")
    if any(len(item) > 500 for item in p.evidence):
        raise ValueError("Evidence references must be at most 500 characters each")
    init_schema()
    c = log._get_db()
    try:
        c.execute("BEGIN IMMEDIATE")
        if p.revises_id is not None:
            parent = c.execute("SELECT author,entry_kind,consent_level FROM thinking_log WHERE id=?", (p.revises_id,)).fetchone()
            if not parent or parent["author"] != p.author or parent["entry_kind"] != "reflection":
                raise ValueError("Revision must reference a reflection by the same author")
            if parent["consent_level"] == "high" and p.consent_level != "high":
                raise ValueError("Revision cannot lower its parent's sensitivity")
        metadata = json.dumps({"uncertainty": p.uncertainty, "changed_mind": p.changed_mind, "evidence": p.evidence})
        decision = cama_trust.classify("inference", "assistant", "\n".join([p.account, p.rationale, metadata]))
        status = "quarantined" if decision["quarantined"] else "recorded"
        now = now_iso()
        cur = c.execute("""INSERT INTO thinking_log
            (author,entry_kind,consent_level,reflection_status,revises_id,author_binding,
             thread_id,what_im_noticing,what_im_weighing,free_notes,created_at,related_log_ids)
            VALUES (?,'reflection',?,?,?,?,?,?,?,?,?,?)""",
            (p.author, p.consent_level, status, p.revises_id, bound_author, p.thread_id,
             p.account, p.rationale, metadata, now,
             json.dumps([p.revises_id] if p.revises_id else [])))
        c.commit()
        return {"stored": True, "id": cur.lastrowid, "author": p.author,
                "status": status, "created_at": now, "revises_id": p.revises_id, "note": NOTE}
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def read(conn, author, limit=10, before_id=None, include_sensitive=False, startup=False):
    # Read-only compatibility with databases created before this feature.
    columns = {r[1] for r in conn.execute("PRAGMA table_info(thinking_log)")}
    if not {"author", "entry_kind", "reflection_status", "revises_id", "consent_level"} <= columns:
        return {"note": NOTE, "records": [], "available": False}
    where = "author=? AND entry_kind='reflection' AND reflection_status='recorded'"
    values = [author]
    if startup or not include_sensitive:
        where += " AND consent_level='low'"
    if startup:
        if "author_binding" not in columns:
            return {"note": NOTE, "records": [], "available": False}
        where += " AND author_binding=author AND NOT EXISTS (SELECT 1 FROM thinking_log newer WHERE newer.revises_id=thinking_log.id AND newer.author=thinking_log.author AND newer.entry_kind='reflection' AND newer.reflection_status='recorded' AND newer.author_binding=newer.author)"
    if before_id is not None:
        where += " AND id<?"
        values.append(before_id)
    limit = 1 if startup else max(1, min(int(limit), 20))
    rows = conn.execute(f"SELECT * FROM thinking_log WHERE {where} ORDER BY id DESC LIMIT ?", values + [limit]).fetchall()
    records = []
    for row in rows:
        if startup:
            entry = {"id": row["id"], "author": row["author"], "account": row["what_im_noticing"][:300],
                     "created_at": row["created_at"], "revises_id": row["revises_id"], "excerpted": True}
            while len(json.dumps(entry, ensure_ascii=True)) > 900 and entry["account"]:
                entry["account"] = entry["account"][:-20]
            records.append(entry)
            continue
        metadata = json.loads(row["free_notes"])
        cap = 400 if startup else 4000
        records.append({"id": row["id"], "author": row["author"],
                        "account": row["what_im_noticing"][:cap],
                        "rationale": row["what_im_weighing"][:cap],
                        "uncertainty": metadata["uncertainty"][:cap],
                        "changed_mind": metadata["changed_mind"][:cap],
                        "evidence": metadata["evidence"][:3] if startup else metadata["evidence"],
                        "created_at": row["created_at"], "revises_id": row["revises_id"],
                        "excerpted": startup, "status": "recorded"})
    return {"note": NOTE, "records": records, "available": True,
            "next_before_id": rows[-1]["id"] if len(rows) == limit else None}


def register(mcp):
    # Only an explicit reflection write runs the additive metadata migration.
    identity = os.environ.get("CAMA_HIVE_IDENTITY", "").strip().lower()
    bound_author = "lorien" if identity == "codex" else identity or None

    @mcp.tool(name="cama_reflect")
    async def cama_reflect(params: ReflectionInput) -> str:
        """Save a reflection summary as this server's configured author. Revisions append; prior entries stay intact. Separate identities require separate configured server processes."""
        return json.dumps(store(params, bound_author=bound_author))

    @mcp.tool(name="cama_reflections", annotations={"readOnlyHint": True})
    async def cama_reflections(author: str, limit: int = 10, before_id: int | None = None,
                               include_sensitive: bool = False) -> str:
        """Recall one author's reflection history; sensitive entries require explicit opt-in. Earlier accounts may have later revisions."""
        c = log._get_db()
        try:
            return json.dumps(read(c, author, limit, before_id, include_sensitive))
        finally:
            c.close()
