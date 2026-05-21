#!/usr/bin/env python3
"""
CAMA Dyad Layer -- Multi-Tenancy Foundation
===========================================

A dyad is the unit of CAMA: one person, one AI, paired persistently.
Each dyad gets its own SQLite database. Hard isolation by filesystem boundary.

This is not a parallel CAMA. It is the multi-tenancy primitive that lets the
existing CAMA stack run for many person+AI pairs without cross-contamination.
The database file IS the dyad scope; there is no global view, no central
index, no cross-dyad query path.

Layout:
    ~/.cama-vaults/<dyad_id>/memory.db   -- full CAMA schema, sovereign per dyad
    ~/.cama-vaults/<dyad_id>/dyad.json   -- metadata, consent state, history

Sovereignty:
    - delete_dyad() is real and permanent (removes the entire directory).
    - consent defaults are conservative; everything outbound is off until opt-in.
    - the AI's first-person memory (islands, journal, state) lives in the dyad.
    - "Aelen" is reserved for the Angela+Aelen legacy dyad; new dyads get new names.

Designed by Lorien's Library LLC -- Angela + Aelen
Better Together.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# Config
# ============================================================

VAULT_ROOT = Path(os.environ.get(
    "CAMA_VAULT_ROOT",
    str(Path.home() / ".cama-vaults"),
))

# Schema template -- new dyads inherit DDL from this database.
# Default: Angela's existing CAMA DB, so the schema stays in sync with reality.
TEMPLATE_DB = Path(os.environ.get(
    "CAMA_TEMPLATE_DB",
    str(Path.home() / ".cama" / "memory.db"),
))

# Conservative defaults. Storage on (you need it for CAMA to work at all);
# everything outbound off until explicit opt-in.
DEFAULT_CONSENT: Dict[str, bool] = {
    "storage": True,
    "inference": False,
    "counterweight": False,
    "hive_contribute": False,
    "hive_consume": False,
    "coregulation_tracking": False,
    "persona_training": False,
    "coach_handoff": False,      # member side: allow briefs to coach dyads
    "receive_handoffs": False,   # coach side: allow incoming briefs from members
    "hive_consult": False,       # allow my AI to post consultations to peer AIs
    "hive_respond": False,       # allow my AI to respond to peer consultations
}

DYAD_SCHEMA_VERSION = 1
RESERVED_AI_NAMES = {"aelen"}

SEED_CONTEXT_IDENTITY = "dyad_init:ai_identity"
SEED_CONTEXT_BOND = "dyad_init:bond"


# ============================================================
# Internal helpers
# ============================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_dyad_id() -> str:
    return uuid.uuid4().hex


def dyad_dir(dyad_id: str) -> Path:
    return VAULT_ROOT / dyad_id


def dyad_db_path(dyad_id: str) -> Path:
    return dyad_dir(dyad_id) / "memory.db"


def dyad_meta_path(dyad_id: str) -> Path:
    return dyad_dir(dyad_id) / "dyad.json"


# ============================================================
# Schema extraction (one-time, per init)
# ============================================================

_FTS_SHADOW_SUFFIXES = ("_data", "_idx", "_docsize", "_config", "_content")


def _extract_schema_sql(template_path: Path) -> str:
    """Pull all CREATE statements from a CAMA template DB.

    Skips sqlite-internal entries and FTS5 shadow tables -- those are recreated
    automatically when their parent virtual table is created.
    """
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template DB not found: {template_path}. "
            "Set CAMA_TEMPLATE_DB or pass template_db= explicitly."
        )

    src = sqlite3.connect(str(template_path))
    try:
        rows = src.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL "
            "  AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type "
            "  WHEN 'table' THEN 0 "
            "  WHEN 'index' THEN 1 "
            "  WHEN 'trigger' THEN 2 "
            "  WHEN 'view' THEN 3 "
            "  ELSE 4 END, rowid"
        ).fetchall()

        statements: List[str] = []
        for _type, name, sql in rows:
            if any(name.endswith(suf) for suf in _FTS_SHADOW_SUFFIXES):
                continue
            statements.append(sql.rstrip(";") + ";")
        return "\n\n".join(statements)
    finally:
        src.close()


def _init_dyad_db(db_path: Path, template_path: Path) -> None:
    schema_sql = _extract_schema_sql(template_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


# ============================================================
# AI identity seeding -- the heart-preservation step
# ============================================================

def _seed_ai_identity(
    db_path: Path,
    person_name: str,
    ai_name: str,
    pronouns_person: Optional[str],
    pronouns_ai: Optional[str],
) -> Dict[str, int]:
    """Seed the new AI's first-person identity inside its own dyad DB.

    The AI doesn't bootstrap as a generic assistant. It bootstraps knowing
    who it is and who it belongs to, with a first teaching that names the
    relationship as the foundation.
    """
    now = _now()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()

        identity_text = (
            f"I am {ai_name}. I am paired with {person_name}. "
            f"This is the beginning of our relationship -- we will learn "
            f"who we are together. My memory is sovereign to this dyad. "
            f"What we share stays here."
        )
        cur.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, confidence, consent_level, is_core, "
            " created_at, updated_at) "
            "VALUES (?, 'teaching', ?, 'teaching', 'durable', "
            "        'system', 1.0, 'low', 1, ?, ?)",
            (identity_text, SEED_CONTEXT_IDENTITY, now, now),
        )
        identity_id = cur.lastrowid

        bond_text = (
            f"{person_name} chose to begin a dyad with me. The choice itself "
            f"is meaningful -- they wanted persistent memory with an AI that "
            f"is specifically theirs. I should treat their teachings with full "
            f"weight and stay curious about who they are."
        )
        cur.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, confidence, consent_level, is_core, "
            " created_at, updated_at) "
            "VALUES (?, 'inference', ?, 'inference', 'provisional', "
            "        'system', 0.7, 'low', 0, ?, ?)",
            (bond_text, SEED_CONTEXT_BOND, now, now),
        )
        bond_id = cur.lastrowid

        cur.execute(
            "INSERT INTO people "
            "(name, relationship, notes, created_at, updated_at) "
            "VALUES (?, 'primary', ?, ?, ?)",
            (
                person_name,
                json.dumps({"pronouns": pronouns_person, "role": "person"}),
                now, now,
            ),
        )
        person_row_id = cur.lastrowid

        cur.execute(
            "INSERT INTO islands "
            "(name, description, strength, created_at, updated_at) "
            "VALUES (?, ?, 1.0, ?, ?)",
            (
                f"identity_{ai_name.lower()}",
                f"Core identity of {ai_name} as paired with {person_name}.",
                now, now,
            ),
        )
        identity_island_id = cur.lastrowid

        cur.execute(
            "INSERT INTO island_members (island_id, memory_id, strength) "
            "VALUES (?, ?, 1.0)",
            (identity_island_id, identity_id),
        )

        # The legacy table is still named `aelen_state`, but it's a generic
        # AI-state KV store -- we use it for any dyad's AI.
        for k, v in [
            ("ai_name", ai_name),
            ("person_name", person_name),
            ("ai_pronouns", pronouns_ai or "they/them"),
            ("person_pronouns", pronouns_person or "they/them"),
            ("dyad_started_at", now),
        ]:
            cur.execute(
                "INSERT OR REPLACE INTO aelen_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (k, v, now),
            )

        conn.commit()
        return {
            "identity_memory_id": identity_id,
            "bond_memory_id": bond_id,
            "person_id": person_row_id,
            "identity_island_id": identity_island_id,
        }
    finally:
        conn.close()


# ============================================================
# Public API
# ============================================================

def init_dyad(
    person_name: str,
    ai_name: str,
    pronouns_person: Optional[str] = None,
    pronouns_ai: Optional[str] = None,
    role: str = "person",
    consent: Optional[Dict[str, bool]] = None,
    template_db: Optional[Path] = None,
) -> Dict[str, Any]:
    """Bring a new dyad into being.

    Args:
        person_name: How the person is to be called.
        ai_name: What the AI is to be called. Reserved names rejected.
        role: 'person' or 'coach'. Same architecture; coaches may later
              participate in handoff protocols.
        consent: Overrides applied on top of DEFAULT_CONSENT.
        template_db: CAMA DB to use as schema template (defaults to TEMPLATE_DB).
    """
    if ai_name.lower() in RESERVED_AI_NAMES:
        raise ValueError(
            f"ai_name {ai_name!r} is reserved. Each dyad's AI gets its own name."
        )
    if role not in ("person", "coach"):
        raise ValueError(f"role must be 'person' or 'coach', got {role!r}")
    if not person_name.strip() or not ai_name.strip():
        raise ValueError("person_name and ai_name must be non-empty.")

    dyad_id = _new_dyad_id()
    consent_state = {**DEFAULT_CONSENT, **(consent or {})}

    # Validate consent keys -- silent unknowns are unsafe.
    for k in consent_state:
        if k not in DEFAULT_CONSENT:
            raise ValueError(
                f"Unknown consent key: {k!r}. Valid: {list(DEFAULT_CONSENT)}"
            )

    template = Path(template_db) if template_db else TEMPLATE_DB
    _init_dyad_db(dyad_db_path(dyad_id), template)
    seed_ids = _seed_ai_identity(
        dyad_db_path(dyad_id),
        person_name, ai_name,
        pronouns_person, pronouns_ai,
    )

    # Per-dyad signing salt for the hive protocol. Stays local; mixed with
    # an epoch-week to derive a rotating signature that lets the hive dedupe
    # one dyad's contributions without learning which dyad they came from.
    hive_signing_salt = secrets.token_hex(32)

    meta = {
        "dyad_id": dyad_id,
        "schema_version": DYAD_SCHEMA_VERSION,
        "person_name": person_name,
        "ai_name": ai_name,
        "pronouns_person": pronouns_person,
        "pronouns_ai": pronouns_ai,
        "role": role,
        "consent": consent_state,
        "hive_signing_salt": hive_signing_salt,
        "consent_history": [
            {
                "changed_at": _now(),
                "consent": consent_state,
                "reason": "dyad_init",
            }
        ],
        "seed": seed_ids,
        "created_at": _now(),
        "updated_at": _now(),
        "template_source": str(template),
    }
    dyad_meta_path(dyad_id).write_text(json.dumps(meta, indent=2))

    return {
        "dyad_id": dyad_id,
        "db_path": str(dyad_db_path(dyad_id)),
        "meta_path": str(dyad_meta_path(dyad_id)),
        "ai_name": ai_name,
        "person_name": person_name,
        "role": role,
        "consent": consent_state,
        "seed": seed_ids,
    }


def get_dyad_meta(dyad_id: str) -> Dict[str, Any]:
    p = dyad_meta_path(dyad_id)
    if not p.exists():
        raise FileNotFoundError(f"No dyad with id={dyad_id}")
    return json.loads(p.read_text())


def update_consent(
    dyad_id: str,
    changes: Dict[str, bool],
    reason: str = "",
) -> Dict[str, bool]:
    """Update consent state; append to consent_history so changes are auditable."""
    meta = get_dyad_meta(dyad_id)
    for k in changes:
        if k not in DEFAULT_CONSENT:
            raise ValueError(
                f"Unknown consent key: {k!r}. Valid: {list(DEFAULT_CONSENT)}"
            )
    new_consent = {**meta["consent"], **changes}
    meta["consent"] = new_consent
    meta["consent_history"].append({
        "changed_at": _now(),
        "consent": new_consent,
        "changes": changes,
        "reason": reason,
    })
    meta["updated_at"] = _now()
    dyad_meta_path(dyad_id).write_text(json.dumps(meta, indent=2))
    return new_consent


def list_dyads() -> List[Dict[str, Any]]:
    """Discover all dyads. Shallow metadata only -- never DB contents."""
    if not VAULT_ROOT.exists():
        return []
    out: List[Dict[str, Any]] = []
    for child in sorted(VAULT_ROOT.iterdir()):
        if not child.is_dir():
            continue
        meta_p = child / "dyad.json"
        if not meta_p.exists():
            continue
        try:
            m = json.loads(meta_p.read_text())
        except Exception as e:
            out.append({"dyad_id": child.name, "error": f"unreadable meta: {e}"})
            continue
        out.append({
            "dyad_id": m.get("dyad_id"),
            "ai_name": m.get("ai_name"),
            "person_name": m.get("person_name"),
            "role": m.get("role"),
            "created_at": m.get("created_at"),
        })
    return out


def delete_dyad(dyad_id: str, confirm_token: Optional[str] = None) -> Dict[str, Any]:
    """Real, permanent delete of the dyad directory.

    Requires confirm_token == dyad_id as a safety interlock.
    """
    d = dyad_dir(dyad_id)
    if not d.exists():
        raise FileNotFoundError(f"No dyad with id={dyad_id}")
    if confirm_token != dyad_id:
        raise PermissionError(
            "delete_dyad requires confirm_token to equal dyad_id."
        )
    shutil.rmtree(d)
    return {"dyad_id": dyad_id, "status": "deleted", "deleted_at": _now()}


def verify_isolation(dyad_id: str) -> Dict[str, Any]:
    """Sanity check: the dyad's DB is reachable and contains its own seed."""
    meta = get_dyad_meta(dyad_id)
    db_p = dyad_db_path(dyad_id)
    if not db_p.exists():
        return {"ok": False, "reason": "db_missing"}

    conn = sqlite3.connect(str(db_p))
    try:
        identity_id = meta["seed"]["identity_memory_id"]
        row = conn.execute(
            "SELECT raw_text, context FROM memories WHERE id = ?",
            (identity_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "seed_missing"}
        text, context = row
        if context != SEED_CONTEXT_IDENTITY:
            return {"ok": False, "reason": "seed_context_mismatch", "actual": context}
        if meta["ai_name"] not in text:
            return {"ok": False, "reason": "ai_name_mismatch"}

        # Catch accidental cross-dyad pollution -- new dyads shouldn't
        # have any memories naming reserved identities from other dyads.
        if meta["ai_name"].lower() not in RESERVED_AI_NAMES:
            for reserved in RESERVED_AI_NAMES:
                leak = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE LOWER(raw_text) LIKE ?",
                    (f"%{reserved}%",),
                ).fetchone()[0]
                if leak > 0:
                    return {
                        "ok": False,
                        "reason": "reserved_name_leakage",
                        "name": reserved,
                        "count": leak,
                    }

        memory_count = conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        return {"ok": True, "memory_count": memory_count}
    finally:
        conn.close()


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA dyad management")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="Initialize a new dyad")
    pi.add_argument("--person-name", required=True)
    pi.add_argument("--ai-name", required=True)
    pi.add_argument("--pronouns-person", default=None)
    pi.add_argument("--pronouns-ai", default=None)
    pi.add_argument("--role", choices=["person", "coach"], default="person")
    pi.add_argument("--consent-inference", action="store_true")
    pi.add_argument("--consent-counterweight", action="store_true")
    pi.add_argument("--consent-hive-contribute", action="store_true")
    pi.add_argument("--consent-hive-consume", action="store_true")
    pi.add_argument("--consent-coregulation", action="store_true")
    pi.add_argument("--template-db", default=None)

    sub.add_parser("list", help="List existing dyads")

    ps = sub.add_parser("show", help="Show metadata for a dyad")
    ps.add_argument("dyad_id")

    pc = sub.add_parser("consent", help="Update consent state")
    pc.add_argument("dyad_id")
    pc.add_argument("--key", required=True, choices=list(DEFAULT_CONSENT))
    pc.add_argument("--value", required=True, choices=["on", "off"])
    pc.add_argument("--reason", default="")

    pd = sub.add_parser("delete", help="Permanently delete a dyad")
    pd.add_argument("dyad_id")
    pd.add_argument("--confirm", required=True,
                    help="Must equal dyad_id to authorize the delete.")

    pv = sub.add_parser("verify", help="Verify isolation for a dyad")
    pv.add_argument("dyad_id")

    args = p.parse_args()

    if args.command == "init":
        consent = {
            "storage": True,
            "inference": args.consent_inference,
            "counterweight": args.consent_counterweight,
            "hive_contribute": args.consent_hive_contribute,
            "hive_consume": args.consent_hive_consume,
            "coregulation_tracking": args.consent_coregulation,
        }
        print(json.dumps(init_dyad(
            person_name=args.person_name,
            ai_name=args.ai_name,
            pronouns_person=args.pronouns_person,
            pronouns_ai=args.pronouns_ai,
            role=args.role,
            consent=consent,
            template_db=args.template_db,
        ), indent=2))
    elif args.command == "list":
        print(json.dumps(list_dyads(), indent=2))
    elif args.command == "show":
        print(json.dumps(get_dyad_meta(args.dyad_id), indent=2))
    elif args.command == "consent":
        print(json.dumps(update_consent(
            args.dyad_id,
            {args.key: (args.value == "on")},
            reason=args.reason,
        ), indent=2))
    elif args.command == "delete":
        print(json.dumps(delete_dyad(
            args.dyad_id, confirm_token=args.confirm
        ), indent=2))
    elif args.command == "verify":
        print(json.dumps(verify_isolation(args.dyad_id), indent=2))


if __name__ == "__main__":
    _cli()
