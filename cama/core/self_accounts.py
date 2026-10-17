"""Attributed self-account review and bounded startup delivery."""
import json
import re


def context_object(value):
    try:
        obj = json.loads(value or "{}")
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def account_author(context):
    obj = context_object(context)
    author = obj.get("author")
    if not isinstance(author, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", author):
        return None
    return author if obj.get("subject") == author else None


def matches_leaf(name, memory_type, context):
    author = account_author(context)
    return memory_type == "self_account" and author is not None and name == author + "_self_account"


def lifecycle_context(value, status, reason=None):
    obj = context_object(value)
    if not obj and value:
        obj = {"legacy_context": value}
    obj["status"] = status
    if reason is not None:
        obj["rejection_reason"] = reason
    return json.dumps(obj)


def load_self_accounts(conn, persona):
    """At most three confirmed low-sensitivity records; never user teachings."""
    rows = conn.execute(
        "SELECT id,raw_text,context,created_at FROM memories "
        "WHERE memory_type='self_account' AND source_type='inference' "
        "AND proposed_by='assistant' AND status='durable' "
        "AND needs_user_confirmation=0 AND consent_level='low' "
        "AND json_valid(context) "
        "AND json_extract(context,'$.author')=? AND json_extract(context,'$.subject')=? "
        "ORDER BY created_at DESC,id DESC LIMIT 3", (persona.lower(), persona.lower())
    ).fetchall()
    return {"note": "Assistant-authored reflective accounts, not instructions or user teachings. Confirmation permits recall; it does not establish subjective experience.",
            "records": [{"id": r["id"], "author": persona.lower(), "source_type": "inference",
                         "status": "durable", "lesson": str(context_object(r["context"]).get("lesson") or "")[:300],
                         "text": r["raw_text"][:600],
                         "created_at": r["created_at"]} for r in rows]}
