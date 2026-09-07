"""
CAMA typed-token representation
===============================

A CAMA memory is not plain text. It carries provenance (teaching vs
inference, who proposed it, durable vs provisional), a trust score, the
people involved, and an affect signature (valence / arousal / emotions).

Historically all of that lived in side columns the language model never
saw: retrieval flattened a memory to ``- [type] raw_text`` and dropped the
rest. This module serializes those typed fields INTO the token stream as
first-class tokens, so a model reading retrieved context can see *how much
to trust a memory and how it was felt*, not just what it said.

    encode_memory(record, affect=None) -> str   serialize a memory record
    parse_typed_tokens(stream)         -> dict   recover the structured part
    SPECIAL_TOKENS                              fixed vocab to register on a
                                                tokenizer IF a model is ever
                                                trained to consume this (the
                                                parked next step).

The encoding is deliberately lossy on the continuous values: valence,
arousal, trust and emotion intensities are bucketed, because tokens are
discrete and the model only needs the gist ("strongly negative, high
trust"), not float precision. The exact floats stay in the DB.

This module imports nothing from the rest of CAMA and has no heavy deps,
so it is safe to import anywhere (mirrors cama_user_paths' discipline).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# ----------------------------------------------------------------------
# Vocabulary
# ----------------------------------------------------------------------
# Structural + fixed-category special tokens. KIND:*, BY:*, WHO:* and
# EMO:* names are templated (open vocabulary) and intentionally not all
# enumerated here; the fixed buckets below are what a tokenizer must know
# to keep them atomic.
SPECIAL_TOKENS: List[str] = [
    "<MEM>", "</MEM>", "<TXT>", "</TXT>",
    "<SRC:teaching>", "<SRC:inference>",
    "<BY:user>", "<BY:assistant>", "<BY:aelen>", "<BY:system>",
    "<DURABLE>", "<PROVISIONAL>", "<CORE>",
    "<TRUST:lo>", "<TRUST:md>", "<TRUST:hi>",
    "<VAL:-2>", "<VAL:-1>", "<VAL:0>", "<VAL:+1>", "<VAL:+2>",
    "<ARO:lo>", "<ARO:md>", "<ARO:hi>",
]

# Cap on how many emotion tokens to emit per memory, strongest first.
_MAX_EMOTIONS = 3
# Minimum emotion intensity worth a token.
_EMO_FLOOR = 0.4

_TOKEN_SAFE = re.compile(r"[^a-z0-9_]+")


def _slug(value: Any) -> str:
    """Token-safe slug: lowercase, non-alphanumerics collapsed to '_'."""
    return _TOKEN_SAFE.sub("_", str(value).strip().lower()).strip("_")


# ----------------------------------------------------------------------
# Bucketing: continuous values -> discrete tokens
# ----------------------------------------------------------------------

def _bucket_valence(x: Optional[float]) -> str:
    if x is None:
        return "0"
    if x <= -0.6:
        return "-2"
    if x <= -0.2:
        return "-1"
    if x < 0.2:
        return "0"
    if x < 0.6:
        return "+1"
    return "+2"


def _bucket_level(x: Optional[float]) -> str:
    """[0, 1] (or [-1, 1], clamped at 0) -> lo / md / hi."""
    if x is None:
        return "lo"
    if x < 0.34:
        return "lo"
    if x < 0.67:
        return "md"
    return "hi"


# ----------------------------------------------------------------------
# People
# ----------------------------------------------------------------------
# Memories may carry people in a dedicated list, or only inside the
# free-text ``context`` as "... | People: angela, clarence".
_PEOPLE_RE = re.compile(r"People:\s*([^|]+)", re.IGNORECASE)


def _people(record: Dict[str, Any]) -> List[str]:
    people = record.get("people")
    if isinstance(people, (list, tuple)):
        names = [str(p) for p in people]
    elif isinstance(people, str) and people.strip():
        names = [p for p in re.split(r"[;,]", people)]
    else:
        names = []
        ctx = record.get("context") or ""
        m = _PEOPLE_RE.search(ctx)
        if m:
            names = [p for p in re.split(r"[;,]", m.group(1))]
    out: List[str] = []
    seen = set()
    for n in names:
        s = _slug(n)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ----------------------------------------------------------------------
# Encode
# ----------------------------------------------------------------------

def encode_memory(record: Dict[str, Any], affect: Optional[Dict[str, Any]] = None) -> str:
    """Serialize a CAMA memory record into a typed-token stream.

    ``record`` is a memories-table row as a dict (any subset of the columns
    is tolerated). ``affect`` is the dict returned by the affect getter
    ({valence, arousal, emotions{...}}); if omitted, an ``affect`` key on
    the record is used, else affect tokens are skipped.
    """
    affect = affect if affect is not None else record.get("affect")
    parts: List[str] = ["<MEM>"]

    kind = record.get("memory_type")
    if kind:
        parts.append(f"<KIND:{_slug(kind)}>")

    src = record.get("source_type")
    if src in ("teaching", "inference"):
        parts.append(f"<SRC:{src}>")

    by = record.get("proposed_by")
    if by:
        parts.append(f"<BY:{_slug(by)}>")

    status = record.get("status")
    if status == "durable":
        parts.append("<DURABLE>")
    elif status in ("provisional", "pending"):
        parts.append("<PROVISIONAL>")

    if record.get("is_core"):
        parts.append("<CORE>")

    if record.get("trust_score") is not None:
        parts.append(f"<TRUST:{_bucket_level(record.get('trust_score'))}>")

    for who in _people(record):
        parts.append(f"<WHO:{who}>")

    if affect:
        if affect.get("valence") is not None:
            parts.append(f"<VAL:{_bucket_valence(affect.get('valence'))}>")
        if affect.get("arousal") is not None:
            aro = affect.get("arousal")
            parts.append(f"<ARO:{_bucket_level(abs(aro) if aro is not None else None)}>")
        emotions = affect.get("emotions") or {}
        ranked = sorted(emotions.items(), key=lambda kv: kv[1], reverse=True)
        for name, score in ranked[:_MAX_EMOTIONS]:
            if score is not None and score >= _EMO_FLOOR:
                parts.append(f"<EMO:{_slug(name)}:{_bucket_level(score)}>")

    text = (record.get("raw_text") or record.get("text") or "").strip()
    parts.append("<TXT>")
    parts.append(text)
    parts.append("</TXT>")
    parts.append("</MEM>")
    return " ".join(parts)


# ----------------------------------------------------------------------
# Decode (round-trip of the structured part)
# ----------------------------------------------------------------------

_TAG_RE = re.compile(r"<(/?)([A-Z]+)(?::([^>]+))?>")
_TXT_RE = re.compile(r"<TXT>(.*?)</TXT>", re.DOTALL)


def parse_typed_tokens(stream: str) -> Dict[str, Any]:
    """Recover the structured fields from an encoded stream.

    Continuous values come back as their buckets, by design; the text comes
    back verbatim. Useful for tests and for any consumer that wants the
    metadata without re-querying the DB.
    """
    out: Dict[str, Any] = {"people": [], "emotions": []}
    m = _TXT_RE.search(stream)
    out["text"] = m.group(1).strip() if m else ""
    for slash, tag, val in _TAG_RE.findall(stream):
        if slash or tag == "TXT":
            continue
        if tag == "KIND":
            out["memory_type"] = val
        elif tag == "SRC":
            out["source_type"] = val
        elif tag == "BY":
            out["proposed_by"] = val
        elif tag == "DURABLE":
            out["status"] = "durable"
        elif tag == "PROVISIONAL":
            out["status"] = "provisional"
        elif tag == "CORE":
            out["is_core"] = True
        elif tag == "TRUST":
            out["trust"] = val
        elif tag == "WHO":
            out["people"].append(val)
        elif tag == "VAL":
            out["valence_bucket"] = val
        elif tag == "ARO":
            out["arousal_bucket"] = val
        elif tag == "EMO" and val:
            name, _, level = val.rpartition(":")
            out["emotions"].append((name or val, level))
    return out


if __name__ == "__main__":
    # Self-contained demo on SYNTHETIC records (no real memory is read).
    demo = {
        "memory_type": "identity",
        "source_type": "teaching",
        "proposed_by": "user",
        "status": "durable",
        "is_core": 1,
        "trust_score": 1.0,
        "context": "Conversation: longest thread | People: angela, aelen",
        "raw_text": "Angela built CAMA on the worst day. Nobody made her do it.",
        "affect": {"valence": 0.7, "arousal": 0.4,
                   "emotions": {"determination": 0.9, "pride": 0.7, "love": 0.5}},
    }
    encoded = encode_memory(demo)
    print("ENCODED:\n", encoded, "\n")
    print("PARSED:\n", parse_typed_tokens(encoded))
