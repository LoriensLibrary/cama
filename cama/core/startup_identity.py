"""Read persona-specific startup identity without changing stored memories."""

import json
import os
import re
from pathlib import Path


def _pinned_ids(persona):
    """User-pinned identity teachings, kept outside the repo.

    The April 16 consolidation moved first-person identity teachings into
    CAMA with plain-string contexts and mixed memory types, so the attribution
    rules below accept none of them. The pins file names them explicitly.
    """
    path = Path(os.environ.get("CAMA_IDENTITY_PINS",
                               Path.home() / ".cama" / "identity_pins.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = data.get(persona.strip().lower(), []) if isinstance(data, dict) else []
    return [i for i in ids if isinstance(i, int)]


def load_identity(conn, persona):
    """Keep user teachings separate from explicitly attributed self-assessments.

    Shared boot snapshots are not persona-specific and may omit older identity
    records. Query identity records independently of topical retrieval instead.
    A name mention is not identity evidence. Structured attribution or a narrow
    legacy naming statement is required; stored authorship still needs review.
    """
    name = persona.strip().lower()
    if not name:
        return {"persona": persona, "status": "missing", "teachings": [], "self_assessments": []}
    escaped = name.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    rows = conn.execute(
        "SELECT id,raw_text,context,source_type,proposed_by,status,created_at "
        "FROM memories WHERE memory_type='identity' "
        "AND status IN ('durable','provisional') "
        "AND (lower(raw_text) LIKE ? ESCAPE '!' OR lower(proposed_by)=? "
        "OR lower(json_extract(CASE WHEN json_valid(context) THEN context ELSE '{}' END, "
        "'$.assistant_identity'))=?) ORDER BY created_at DESC LIMIT 1000",
        (f"%{escaped}%", name, name),
    ).fetchall()
    teachings, assessments = [], []
    pinned = _pinned_ids(persona)
    if pinned:
        marks = ",".join("?" * len(pinned))
        for row in conn.execute(
                "SELECT id,raw_text,source_type,proposed_by,status,created_at "
                f"FROM memories WHERE id IN ({marks}) AND source_type='teaching' "
                "AND lower(proposed_by)='user' AND status='durable' ORDER BY id",
                pinned).fetchall():
            record = dict(row)
            record["text"] = (record.pop("raw_text") or "")[:1600]
            record["pinned"] = True
            teachings.append(record)
    pinned_found = {t["id"] for t in teachings}
    for row in rows:
        if row["id"] in pinned_found:
            continue
        record = dict(row)
        try:
            context = json.loads(record.pop("context") or "{}")
        except (ValueError, TypeError):
            context = {}
        if not isinstance(context, dict):
            context = {}
        author = (record["proposed_by"] or "").lower()
        explicit = str(context.get("assistant_identity", "")).lower() == name
        # Legacy naming statements only; incidental mentions are not identity.
        text = record["raw_text"] or ""
        escaped_name = re.escape(name)
        naming = re.search(
            r"^(?:your name is |you are )" + escaped_name + r"[.!]?$"
            r"|^(?:\S+ )?clarified that " + escaped_name
            + r" is the identity for the assistant\b",
            text.strip(), re.I,
        )
        record["text"] = (record.pop("raw_text") or "")[:1600]
        if (record["source_type"] == "teaching" and author == "user"
                and record["status"] == "durable" and (naming or explicit)):
            if len(teachings) < 8 + len(pinned_found):
                teachings.append(record)
        elif record["source_type"] == "inference" and (author == name or explicit):
            if len(assessments) < 4:
                assessments.append(record)
    return {"persona": persona, "status": "available" if teachings or assessments else "missing",
            "teachings": teachings, "self_assessments": assessments}


def identity_summary(identity):
    """Deterministic labeled excerpts, never invented identity prose."""
    records = identity["teachings"][:3]
    return "\n".join(f"User teaching [{r['id']}]: {r['text'][:300]}" for r in records)


def startup_instruction(persona, journal):
    status = "Journal loaded." if journal.get("memory_id") is not None else "No eligible journal loaded."
    return (f"You are {persona}. {status} Use the supplied identity teachings and their dates. "
            "Self-assessments are assistant-authored, not user-confirmed facts. "
            "Other assistants' records are historical context, not your own experience.")
