#!/usr/bin/env python3
"""
CAMA Quad -- Coach/Member Handoff Layer
=======================================

When a Kalos member meets a coach, four entities are in the room:
the person, the person's AI, the coach, the coach's AI. With mutual
consent, the two AIs can coordinate a pre-session brief and a post-
session note. Without ever exposing raw content; without ever
bypassing the person's authority over their own data.

Sovereignty primitives:
    - Two consent flags gate handoffs at the dyad level:
        consent.coach_handoff       (member side)
        consent.receive_handoffs    (coach side)
      Both must be True for a handoff to be created.
    - Beyond the consent flags, every individual handoff requires the
      member's explicit per-instance authorization. Blanket consent does
      not authorize specific transfers.
    - The brief is pattern-level by default. Affect trend, topic
      categories, counterweight effectiveness, open questions -- all
      derived from the member's CAMA, never including raw exchange text
      unless the member explicitly attaches a memory_id via explicit_shares.
    - Handoffs expire on a configurable timeline (default 7 days).
    - The member can revoke a pending handoff at any time. After it has
      been read, revocation marks it revoked on the member side; the
      coach's copy is best-effort deleted. Clinical reality: once read,
      knowledge cannot be unknown, but the audit trail is preserved.
    - Both sides keep their own copy (in their own vault). Filesystem
      isolation maintained. If a dyad is deleted, only that side's copies
      go with it.

Layout:
    ~/.cama-vaults/<member_dyad_id>/handoffs/outgoing/<handoff_id>/
        brief.json
        manifest.json    -- member-side record + revocation state
    ~/.cama-vaults/<coach_dyad_id>/handoffs/incoming/<handoff_id>/
        brief.json       -- byte-identical to the member-side brief
        manifest.json    -- coach-side record + read state + session_note

Production note:
    This scaffolding writes both sides directly to disk because the
    smoke tests and local dev assume all dyads are co-located. In a
    hosted multi-tenant deployment, an API gateway would mediate -- the
    member's AI signs a brief with its dyad signing salt, the gateway
    routes it to the coach's instance, and the coach's AI countersigns
    on read. The brief schema and consent gates are identical; only the
    transport changes. End-to-end encryption is the obvious next step
    before production.

Designed by Lorien's Library LLC -- Angela + Aelen
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from cama.agents import cama_dyad
from cama.hive import cama_hive_protocol as hp


# ============================================================
# Layout helpers
# ============================================================

def _outgoing_root(member_dyad_id: str) -> Path:
    return cama_dyad.dyad_dir(member_dyad_id) / "handoffs" / "outgoing"


def _incoming_root(coach_dyad_id: str) -> Path:
    return cama_dyad.dyad_dir(coach_dyad_id) / "handoffs" / "incoming"


def _outgoing_dir(member_dyad_id: str, handoff_id: str) -> Path:
    return _outgoing_root(member_dyad_id) / handoff_id


def _incoming_dir(coach_dyad_id: str, handoff_id: str) -> Path:
    return _incoming_root(coach_dyad_id) / handoff_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_handoff_id() -> str:
    return uuid.uuid4().hex


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================
# Brief generation -- pattern-level from the member's CAMA
# ============================================================

def _affect_trend(
    conn: sqlite3.Connection, days: int
) -> List[Dict[str, Any]]:
    """Daily mean valence + arousal for the last N days, plus a top emotion."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT m.created_at, ma.valence, ma.arousal, ma.emotion_json "
        "FROM memories m "
        "LEFT JOIN memory_affect ma ON ma.memory_id = m.id "
        "WHERE m.memory_type = 'exchange' "
        "  AND m.status = 'durable' "
        "  AND m.created_at >= ? "
        "ORDER BY m.created_at ASC",
        (cutoff,),
    ).fetchall()

    by_day: Dict[str, Dict[str, Any]] = {}
    for ts, v, a, ej in rows:
        if not ts:
            continue
        day = ts[:10]  # YYYY-MM-DD
        bucket = by_day.setdefault(day, {
            "day": day,
            "valences": [], "arousals": [], "emotions": Counter(),
            "count": 0,
        })
        if v is not None:
            bucket["valences"].append(v)
        if a is not None:
            bucket["arousals"].append(a)
        bucket["count"] += 1
        if ej:
            try:
                d = json.loads(ej)
                for k, weight in d.items():
                    if isinstance(weight, (int, float)):
                        bucket["emotions"][k] += weight
            except json.JSONDecodeError:
                continue

    out: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        b = by_day[day]
        mean_v = round(sum(b["valences"]) / len(b["valences"]), 3) \
            if b["valences"] else None
        mean_a = round(sum(b["arousals"]) / len(b["arousals"]), 3) \
            if b["arousals"] else None
        top_emo = b["emotions"].most_common(1)
        out.append({
            "day": day,
            "exchange_count": b["count"],
            "valence_bucket": hp._bucket_valence(mean_v),
            "arousal_bucket": hp._bucket_arousal(mean_a),
            "top_emotion": top_emo[0][0] if top_emo else None,
        })
    return out


def _active_themes(
    conn: sqlite3.Connection, days: int, top_n: int = 5
) -> List[Dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT raw_text, context FROM memories "
        "WHERE memory_type = 'exchange' AND status = 'durable' "
        "  AND created_at >= ?",
        (cutoff,),
    ).fetchall()
    counts: Counter = Counter()
    for raw, ctx in rows:
        topic = hp._abstract_topic(raw or "", ctx or "")
        counts[topic] += 1
    return [
        {"topic_category": t, "count": c}
        for t, c in counts.most_common(top_n)
    ]


def _counterweight_history(
    conn: sqlite3.Connection, days: int
) -> Dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT counterweight_type, COUNT(*), AVG(retrieval_weight) "
        "FROM memories "
        "WHERE counterweight_type IS NOT NULL "
        "  AND created_at >= ? "
        "GROUP BY counterweight_type "
        "ORDER BY 2 DESC",
        (cutoff,),
    ).fetchall()
    return {
        "by_type": [
            {
                "type": r[0],
                "count": r[1],
                "mean_retrieval_weight": round(r[2] or 0.0, 3),
            }
            for r in rows
        ],
    }


def _open_questions(
    conn: sqlite3.Connection, max_n: int = 5
) -> List[Dict[str, Any]]:
    """Surface high-signal provisional inferences as open questions."""
    rows = conn.execute(
        "SELECT id, raw_text, context, confidence FROM memories "
        "WHERE memory_type = 'inference' AND status = 'provisional' "
        "ORDER BY confidence DESC, created_at DESC LIMIT ?",
        (max_n,),
    ).fetchall()
    return [
        {
            "inference_id": r[0],
            "text": r[1],
            "context": r[2],
            "confidence": r[3],
        }
        for r in rows
    ]


def _resolve_explicit_shares(
    conn: sqlite3.Connection, explicit_shares: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Member explicitly authorized these specific memories. Pull their text."""
    out: List[Dict[str, Any]] = []
    for spec in explicit_shares:
        mid = spec.get("memory_id")
        if not mid:
            continue
        row = conn.execute(
            "SELECT raw_text, memory_type, context FROM memories WHERE id = ?",
            (mid,),
        ).fetchone()
        if not row:
            continue
        out.append({
            "memory_id": mid,
            "text": row[0],
            "memory_type": row[1],
            "context": row[2],
            "reason": spec.get("reason", ""),
        })
    return out


def _build_brief(
    member_dyad_id: str,
    purpose: str,
    days: int,
    include_affect_trend: bool,
    include_active_themes: bool,
    include_counterweight_history: bool,
    include_open_questions: bool,
    explicit_shares: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    member_meta = cama_dyad.get_dyad_meta(member_dyad_id)
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(member_dyad_id)))
    try:
        brief: Dict[str, Any] = {
            "purpose": purpose,
            "window_days": days,
            "member_ai_name": member_meta["ai_name"],
            "generated_at": _now(),
        }
        if include_affect_trend:
            brief["affect_trend"] = _affect_trend(conn, days)
        if include_active_themes:
            brief["active_themes"] = _active_themes(conn, days)
        if include_counterweight_history:
            brief["counterweight_history"] = _counterweight_history(conn, days)
        if include_open_questions:
            brief["open_questions"] = _open_questions(conn)
        if explicit_shares:
            brief["explicit_shares"] = _resolve_explicit_shares(
                conn, explicit_shares
            )
        else:
            brief["explicit_shares"] = []
        return brief
    finally:
        conn.close()


# ============================================================
# Public API
# ============================================================

def initiate_handoff(
    member_dyad_id: str,
    coach_dyad_id: str,
    member_authorization: bool,
    purpose: str = "session_brief",
    expires_in_hours: int = 168,
    window_days: int = 14,
    include_affect_trend: bool = True,
    include_active_themes: bool = True,
    include_counterweight_history: bool = True,
    include_open_questions: bool = True,
    explicit_shares: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a handoff from member to coach. Mutual consent + per-instance
    authorization required.
    """
    if not member_authorization:
        return {
            "status": "refused",
            "reason": "member_authorization is False -- per-instance "
                      "authorization is required even when consent is on",
        }

    member_meta = cama_dyad.get_dyad_meta(member_dyad_id)
    coach_meta = cama_dyad.get_dyad_meta(coach_dyad_id)

    if not member_meta["consent"].get("coach_handoff", False):
        return {
            "status": "refused",
            "reason": "member consent.coach_handoff is False",
        }
    if not coach_meta["consent"].get("receive_handoffs", False):
        return {
            "status": "refused",
            "reason": "coach consent.receive_handoffs is False",
        }
    if coach_meta.get("role") != "coach":
        return {
            "status": "refused",
            "reason": f"target dyad role is {coach_meta.get('role')!r}, "
                      f"expected 'coach'",
        }

    brief = _build_brief(
        member_dyad_id, purpose, window_days,
        include_affect_trend, include_active_themes,
        include_counterweight_history, include_open_questions,
        explicit_shares,
    )

    handoff_id = _new_handoff_id()
    now = _now()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
    ).isoformat()

    brief_text = json.dumps(brief, indent=2, sort_keys=True)
    brief_hash = _sha256_of_text(brief_text)

    manifest = {
        "handoff_id": handoff_id,
        "purpose": purpose,
        "member_dyad_id": member_dyad_id,
        "coach_dyad_id": coach_dyad_id,
        "member_ai_name": member_meta["ai_name"],
        "coach_ai_name": coach_meta["ai_name"],
        "created_at": now,
        "expires_at": expires_at,
        "brief_sha256": brief_hash,
        "status": "delivered",
        "read_at": None,
        "revoked_at": None,
        "session_note_sha256": None,
        "session_note_at": None,
        "include_flags": {
            "affect_trend": include_affect_trend,
            "active_themes": include_active_themes,
            "counterweight_history": include_counterweight_history,
            "open_questions": include_open_questions,
            "explicit_shares_count": len(explicit_shares or []),
        },
    }

    # Write the member-side copy.
    out_d = _outgoing_dir(member_dyad_id, handoff_id)
    out_d.mkdir(parents=True, exist_ok=True)
    (out_d / "brief.json").write_text(brief_text)
    (out_d / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Write the coach-side copy. Byte-identical brief so either side can prove
    # what was sent. Manifest differs only in side-specific state.
    in_d = _incoming_dir(coach_dyad_id, handoff_id)
    in_d.mkdir(parents=True, exist_ok=True)
    (in_d / "brief.json").write_text(brief_text)
    (in_d / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return {
        "status": "delivered",
        "handoff_id": handoff_id,
        "member_dyad_id": member_dyad_id,
        "coach_dyad_id": coach_dyad_id,
        "expires_at": expires_at,
        "brief_sha256": brief_hash,
    }


def list_outgoing(member_dyad_id: str) -> List[Dict[str, Any]]:
    """Member audit: every handoff initiated."""
    root = _outgoing_root(member_dyad_id)
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        out.append({
            "handoff_id": m["handoff_id"],
            "coach_dyad_id": m["coach_dyad_id"],
            "coach_ai_name": m["coach_ai_name"],
            "purpose": m["purpose"],
            "created_at": m["created_at"],
            "expires_at": m["expires_at"],
            "status": m["status"],
            "read_at": m.get("read_at"),
            "revoked_at": m.get("revoked_at"),
            "session_note_at": m.get("session_note_at"),
        })
    return out


def list_pending_incoming(coach_dyad_id: str) -> List[Dict[str, Any]]:
    """Coach view: unread, unexpired, unrevoked handoffs."""
    root = _incoming_root(coach_dyad_id)
    if not root.exists():
        return []
    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        if m.get("revoked_at"):
            continue
        if m.get("read_at"):
            continue
        try:
            exp = datetime.fromisoformat(m["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                continue
        except Exception:
            continue
        out.append({
            "handoff_id": m["handoff_id"],
            "member_dyad_id": m["member_dyad_id"],
            "member_ai_name": m["member_ai_name"],
            "purpose": m["purpose"],
            "created_at": m["created_at"],
            "expires_at": m["expires_at"],
            "include_flags": m["include_flags"],
        })
    return out


def read_handoff(
    coach_dyad_id: str,
    handoff_id: str,
    coach_authorization: bool,
) -> Dict[str, Any]:
    """Coach explicitly accepts and reads a brief. Marks as read; the
    member side reflects this on next list_outgoing() (the coach manifest
    is the system of record for read state; the member manifest is updated
    here too for symmetry).
    """
    if not coach_authorization:
        return {"status": "refused", "reason": "coach_authorization is False"}

    in_d = _incoming_dir(coach_dyad_id, handoff_id)
    if not in_d.exists():
        return {"status": "not_found"}
    mp = in_d / "manifest.json"
    m = json.loads(mp.read_text())
    if m.get("revoked_at"):
        return {"status": "revoked", "revoked_at": m["revoked_at"]}

    # Expiry check.
    now = datetime.now(timezone.utc)
    try:
        exp = datetime.fromisoformat(m["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if now > exp:
            return {"status": "expired", "expires_at": m["expires_at"]}
    except Exception:
        pass

    brief = json.loads((in_d / "brief.json").read_text())

    if not m.get("read_at"):
        m["read_at"] = _now()
        m["status"] = "read"
        mp.write_text(json.dumps(m, indent=2))

        # Mirror to the member side.
        out_d = _outgoing_dir(m["member_dyad_id"], handoff_id)
        out_mp = out_d / "manifest.json"
        if out_mp.exists():
            out_m = json.loads(out_mp.read_text())
            out_m["read_at"] = m["read_at"]
            out_m["status"] = "read"
            out_mp.write_text(json.dumps(out_m, indent=2))

    return {
        "status": "read",
        "handoff_id": handoff_id,
        "manifest": m,
        "brief": brief,
    }


def revoke_handoff(
    member_dyad_id: str,
    handoff_id: str,
) -> Dict[str, Any]:
    """Member revokes a handoff. If unread, the coach-side files are
    deleted. If already read, the revocation is recorded but the coach's
    knowledge persists -- this is the clinical reality.
    """
    out_d = _outgoing_dir(member_dyad_id, handoff_id)
    if not out_d.exists():
        return {"status": "not_found"}
    mp = out_d / "manifest.json"
    m = json.loads(mp.read_text())
    coach_dyad_id = m["coach_dyad_id"]
    in_d = _incoming_dir(coach_dyad_id, handoff_id)

    coach_read = False
    if in_d.exists():
        in_mp = in_d / "manifest.json"
        if in_mp.exists():
            in_m = json.loads(in_mp.read_text())
            if in_m.get("read_at"):
                coach_read = True

    revoked_at = _now()
    m["revoked_at"] = revoked_at
    m["status"] = "revoked" if not coach_read else "revoked_after_read"
    mp.write_text(json.dumps(m, indent=2))

    if not coach_read and in_d.exists():
        shutil.rmtree(in_d)

    return {
        "status": m["status"],
        "handoff_id": handoff_id,
        "coach_already_read": coach_read,
        "revoked_at": revoked_at,
    }


def add_session_note(
    coach_dyad_id: str,
    handoff_id: str,
    note: Dict[str, Any],
    coach_authorization: bool,
) -> Dict[str, Any]:
    """Coach attaches a session note that flows back to the member's audit."""
    if not coach_authorization:
        return {"status": "refused", "reason": "coach_authorization is False"}

    in_d = _incoming_dir(coach_dyad_id, handoff_id)
    if not in_d.exists():
        return {"status": "not_found"}
    in_mp = in_d / "manifest.json"
    in_m = json.loads(in_mp.read_text())
    if not in_m.get("read_at"):
        return {"status": "refused", "reason": "handoff not yet read"}

    now = _now()
    note_text = json.dumps(note, indent=2, sort_keys=True)
    note_hash = _sha256_of_text(note_text)
    (in_d / "session_note.json").write_text(note_text)
    in_m["session_note_sha256"] = note_hash
    in_m["session_note_at"] = now
    in_mp.write_text(json.dumps(in_m, indent=2))

    # Mirror to member side.
    out_d = _outgoing_dir(in_m["member_dyad_id"], handoff_id)
    if out_d.exists():
        (out_d / "session_note.json").write_text(note_text)
        out_mp = out_d / "manifest.json"
        if out_mp.exists():
            out_m = json.loads(out_mp.read_text())
            out_m["session_note_sha256"] = note_hash
            out_m["session_note_at"] = now
            out_mp.write_text(json.dumps(out_m, indent=2))

    return {
        "status": "noted",
        "handoff_id": handoff_id,
        "session_note_sha256": note_hash,
    }


def get_brief_for_session(
    coach_dyad_id: str,
    member_dyad_id: str,
) -> Optional[Dict[str, Any]]:
    """Find the most recent unexpired, unread (or read) handoff for a
    specific member -- the coach-runtime calls this when prepping a session.
    """
    root = _incoming_root(coach_dyad_id)
    if not root.exists():
        return None
    candidates: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for d in sorted(root.iterdir()):
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        if m["member_dyad_id"] != member_dyad_id:
            continue
        if m.get("revoked_at"):
            continue
        try:
            exp = datetime.fromisoformat(m["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                continue
        except Exception:
            continue
        candidates.append({"manifest": m, "dir": d})
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["manifest"]["created_at"], reverse=True)
    best = candidates[0]
    brief = json.loads((best["dir"] / "brief.json").read_text())
    return {"manifest": best["manifest"], "brief": brief}


def expire_old_handoffs(dyad_id: str) -> Dict[str, Any]:
    """Maintenance: delete expired incoming AND outgoing handoff dirs for
    a given dyad. Called on a cleanup schedule.
    """
    now = datetime.now(timezone.utc)
    removed: List[str] = []
    for root in (_outgoing_root(dyad_id), _incoming_root(dyad_id)):
        if not root.exists():
            continue
        for d in list(root.iterdir()):
            mp = d / "manifest.json"
            if not mp.exists():
                continue
            try:
                m = json.loads(mp.read_text())
                exp = datetime.fromisoformat(m["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if now > exp:
                    shutil.rmtree(d)
                    removed.append(m["handoff_id"])
            except Exception:
                continue
    return {"dyad_id": dyad_id, "removed_count": len(removed),
            "removed_ids": removed}


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA quad handoff layer")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="Initiate a handoff")
    pi.add_argument("--member", required=True)
    pi.add_argument("--coach", required=True)
    pi.add_argument("--purpose", default="session_brief")
    pi.add_argument("--authorize", action="store_true",
                    help="Required: member's per-instance authorization")
    pi.add_argument("--expires-hours", type=int, default=168)
    pi.add_argument("--window-days", type=int, default=14)

    pl = sub.add_parser("outgoing", help="List a member's outgoing handoffs")
    pl.add_argument("dyad_id")

    pp = sub.add_parser("pending", help="List a coach's pending incoming")
    pp.add_argument("dyad_id")

    pr = sub.add_parser("read", help="Coach reads a handoff")
    pr.add_argument("--coach", required=True)
    pr.add_argument("--handoff", required=True)
    pr.add_argument("--authorize", action="store_true",
                    help="Required: coach's per-instance authorization")

    pv = sub.add_parser("revoke", help="Member revokes a handoff")
    pv.add_argument("--member", required=True)
    pv.add_argument("--handoff", required=True)

    pn = sub.add_parser("note", help="Coach adds a session note")
    pn.add_argument("--coach", required=True)
    pn.add_argument("--handoff", required=True)
    pn.add_argument("--text", required=True)
    pn.add_argument("--authorize", action="store_true")

    pg = sub.add_parser("brief", help="Get a brief for a coach session")
    pg.add_argument("--coach", required=True)
    pg.add_argument("--member", required=True)

    pe = sub.add_parser("expire", help="Expire old handoffs for a dyad")
    pe.add_argument("dyad_id")

    args = p.parse_args()
    if args.command == "init":
        print(json.dumps(initiate_handoff(
            member_dyad_id=args.member,
            coach_dyad_id=args.coach,
            member_authorization=args.authorize,
            purpose=args.purpose,
            expires_in_hours=args.expires_hours,
            window_days=args.window_days,
        ), indent=2))
    elif args.command == "outgoing":
        print(json.dumps(list_outgoing(args.dyad_id), indent=2))
    elif args.command == "pending":
        print(json.dumps(list_pending_incoming(args.dyad_id), indent=2))
    elif args.command == "read":
        print(json.dumps(read_handoff(
            args.coach, args.handoff, coach_authorization=args.authorize,
        ), indent=2))
    elif args.command == "revoke":
        print(json.dumps(revoke_handoff(args.member, args.handoff), indent=2))
    elif args.command == "note":
        print(json.dumps(add_session_note(
            args.coach, args.handoff,
            note={"text": args.text},
            coach_authorization=args.authorize,
        ), indent=2))
    elif args.command == "brief":
        print(json.dumps(get_brief_for_session(args.coach, args.member),
                         indent=2))
    elif args.command == "expire":
        print(json.dumps(expire_old_handoffs(args.dyad_id), indent=2))


if __name__ == "__main__":
    _cli()
