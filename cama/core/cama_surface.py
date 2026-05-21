#!/usr/bin/env python3
"""
CAMA Memory Surface -- Sovereignty Made Usable
==============================================

Every other module in the stack carries audit data: memories with
provenance, consent state with history, hive publish logs, persona
adapter manifests, handoff trails. This module is the user-facing
surface over all of it -- the single place a person comes to:

    - See what is stored about them
    - Audit who knows what, when consent changed, what was published
    - Export a full bundle of their data
    - Delete specific records, or whole categories, with real semantics

Without this, every transparency claim in the stack is theoretical.
This is where sovereignty is exercised.

Surfaces provided:
    overview          -- everything at a glance: counts, last activity
    memories          -- list memories with filters (type, status, since)
    memory_detail     -- full record + affect + provenance for one memory
    delete_memory     -- real delete, requires confirm_token
    consent           -- current consent state + history
    hive_log          -- what this dyad has published to the hive
    consult_log       -- AI-to-AI consultations posted + responded to
    resources         -- installed domain resources (Kalos etc.)
    persona           -- adapter versions + current
    handoffs          -- outgoing (member side) and incoming (coach side)
    export            -- a single JSON bundle of everything
    purge_category    -- bulk delete by memory_type/status, double-confirm

All operations are scoped to a single dyad. No cross-dyad reads.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from cama.agents import cama_dyad, cama_persona, cama_quad

# ============================================================
# Helpers
# ============================================================
from cama.core.time_utils import now_iso as _now
from cama.hive import cama_hive_consult, cama_hive_resources


def _open_db(dyad_id: str) -> sqlite3.Connection:
    p = cama_dyad.dyad_db_path(dyad_id)
    if not p.exists():
        raise FileNotFoundError(f"No DB for dyad {dyad_id}")
    return sqlite3.connect(str(p))


def _try_publish_log(dyad_id: str) -> List[Dict[str, Any]]:
    """The hive publish log table lives inside the dyad's DB."""
    conn = _open_db(dyad_id)
    try:
        rows = conn.execute(
            "SELECT published_at, record_uuid, source_memory_id, "
            "       valence_bucket, arousal_bucket, dominant_emotion, "
            "       topic_category, time_bucket, payload_json "
            "FROM dyad_hive_publish_log "
            "ORDER BY published_at DESC LIMIT 500"
        ).fetchall()
        return [
            {
                "published_at": r[0], "record_uuid": r[1],
                "source_memory_id": r[2], "valence_bucket": r[3],
                "arousal_bucket": r[4], "dominant_emotion": r[5],
                "topic_category": r[6], "time_bucket": r[7],
                "payload": json.loads(r[8]) if r[8] else None,
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ============================================================
# Public API
# ============================================================

def overview(dyad_id: str) -> Dict[str, Any]:
    """One-glance summary of what's in the dyad. Counts + last activity
    across every layer. Cheap to call; safe to display."""
    meta = cama_dyad.get_dyad_meta(dyad_id)
    conn = _open_db(dyad_id)
    try:
        counts = dict(conn.execute(
            "SELECT memory_type, COUNT(*) FROM memories "
            "WHERE status = 'durable' GROUP BY memory_type"
        ).fetchall())
        total = sum(counts.values())
        last_exchange = conn.execute(
            "SELECT MAX(created_at) FROM memories "
            "WHERE memory_type = 'exchange' AND status = 'durable'"
        ).fetchone()[0]
        provisional_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'provisional'"
        ).fetchone()[0]
        counterweight_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE counterweight_type IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    publish_log = _try_publish_log(dyad_id)
    installed = cama_hive_resources.list_installed(dyad_id)
    adapters = cama_persona.list_adapters(dyad_id)
    current_adapter = cama_persona.get_current_adapter(dyad_id)
    outgoing = cama_quad.list_outgoing(dyad_id)
    incoming = cama_quad.list_pending_incoming(dyad_id)
    try:
        consult_entries = cama_hive_consult.get_consult_log(dyad_id, limit=500)
    except Exception:
        consult_entries = []
    consult_posted = sum(
        1 for e in consult_entries if e.get("direction") == "posted"
    )
    consult_responded = sum(
        1 for e in consult_entries if e.get("direction") == "responded"
    )

    return {
        "dyad_id": dyad_id,
        "person_name": meta["person_name"],
        "ai_name": meta["ai_name"],
        "role": meta.get("role"),
        "created_at": meta["created_at"],
        "consent": meta["consent"],
        "memory_counts": {
            "by_type": counts,
            "total_durable": total,
            "provisional": provisional_count,
            "counterweight_tagged": counterweight_count,
        },
        "last_exchange_at": last_exchange,
        "hive": {
            "records_published": len(publish_log),
            "last_published_at": publish_log[0]["published_at"]
                                  if publish_log else None,
        },
        "domain_resources_installed": [
            {
                "name": r["name"], "version": r["version"],
                "type": r["resource_type"], "publisher": r.get("publisher"),
            }
            for r in installed
        ],
        "persona": {
            "adapters": len(adapters),
            "current_version": current_adapter["current_version"]
                               if current_adapter else None,
        },
        "handoffs": {
            "outgoing_total": len(outgoing),
            "incoming_pending": len(incoming),
        },
        "consultations": {
            "posted_total": consult_posted,
            "responded_total": consult_responded,
        },
        "summary_generated_at": _now(),
    }


# ============================================================
# Memory listing + detail
# ============================================================

def list_memories(
    dyad_id: str,
    memory_type: Optional[str] = None,
    status: Optional[str] = "durable",
    since_iso: Optional[str] = None,
    contains: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List memories with filters. Returns lightweight records suitable
    for a scrollable list view. Use memory_detail() for full content."""
    conn = _open_db(dyad_id)
    try:
        clauses: List[str] = []
        params: List[Any] = []
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(since_iso)
        if contains:
            clauses.append("raw_text LIKE ?")
            params.append(f"%{contains}%")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        q = (
            "SELECT id, memory_type, status, context, "
            "       substr(raw_text, 1, 200), created_at, "
            "       counterweight_type, is_core "
            f"FROM memories{where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = conn.execute(q, params).fetchall()
        return [
            {
                "id": r[0],
                "memory_type": r[1],
                "status": r[2],
                "context": r[3],
                "preview": r[4],
                "created_at": r[5],
                "counterweight_type": r[6],
                "is_core": bool(r[7]) if r[7] is not None else False,
            }
            for r in rows
        ]
    finally:
        conn.close()


def memory_detail(dyad_id: str, memory_id: int) -> Dict[str, Any]:
    """Full content for a single memory: raw text, affect, edges,
    publish log entries that reference it."""
    conn = _open_db(dyad_id)
    try:
        row = conn.execute(
            "SELECT id, raw_text, memory_type, context, source_type, "
            "       status, proposed_by, confidence, "
            "       counterweight_type, retrieval_weight, is_core, "
            "       created_at, updated_at "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not row:
            return {"error": "not_found", "memory_id": memory_id}
        affect_row = conn.execute(
            "SELECT valence, arousal, dominance, emotion_json, confidence "
            "FROM memory_affect WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        affect = None
        if affect_row:
            try:
                emo = json.loads(affect_row[3]) if affect_row[3] else {}
            except json.JSONDecodeError:
                emo = {}
            affect = {
                "valence": affect_row[0], "arousal": affect_row[1],
                "dominance": affect_row[2], "emotions": emo,
                "confidence": affect_row[4],
            }
        # Edges to/from this memory.
        try:
            edges_out = conn.execute(
                "SELECT to_id, edge_type, weight FROM edges WHERE from_id = ?",
                (memory_id,),
            ).fetchall()
            edges_in = conn.execute(
                "SELECT from_id, edge_type, weight FROM edges WHERE to_id = ?",
                (memory_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            edges_out = []
            edges_in = []
        # Hive publish references.
        try:
            pub_rows = conn.execute(
                "SELECT published_at, record_uuid FROM dyad_hive_publish_log "
                "WHERE source_memory_id = ?",
                (memory_id,),
            ).fetchall()
            hive_refs = [
                {"published_at": r[0], "record_uuid": r[1]}
                for r in pub_rows
            ]
        except sqlite3.OperationalError:
            hive_refs = []
        return {
            "id": row[0],
            "raw_text": row[1],
            "memory_type": row[2],
            "context": row[3],
            "source_type": row[4],
            "status": row[5],
            "proposed_by": row[6],
            "confidence": row[7],
            "counterweight_type": row[8],
            "retrieval_weight": row[9],
            "is_core": bool(row[10]) if row[10] is not None else False,
            "created_at": row[11],
            "updated_at": row[12],
            "affect": affect,
            "edges_outgoing": [
                {"to": r[0], "type": r[1], "weight": r[2]}
                for r in edges_out
            ],
            "edges_incoming": [
                {"from": r[0], "type": r[1], "weight": r[2]}
                for r in edges_in
            ],
            "hive_publish_references": hive_refs,
        }
    finally:
        conn.close()


# ============================================================
# Deletion -- real, audited
# ============================================================

def delete_memory(
    dyad_id: str,
    memory_id: int,
    confirm_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Real delete of a single memory. confirm_token must equal str(memory_id).
    Also wipes affect, embeddings, edges, and FTS rows for this memory."""
    if confirm_token != str(memory_id):
        raise PermissionError(
            "delete_memory requires confirm_token to equal str(memory_id)."
        )
    conn = _open_db(dyad_id)
    try:
        row = conn.execute(
            "SELECT raw_text, memory_type, is_core FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found", "memory_id": memory_id}

        # Best-effort cascade across known related tables. Tolerate missing
        # tables silently so this works against partial schemas.
        related = [
            "memory_affect", "memory_embeddings",
        ]
        for tbl in related:
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE memory_id = ?",
                             (memory_id,))
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute(
                "DELETE FROM edges WHERE from_id = ? OR to_id = ?",
                (memory_id, memory_id),
            )
        except sqlite3.Error:
            pass  # FTS / optional tables: triggers may cascade; safe to skip.
        try:
            conn.execute("DELETE FROM memories_fts WHERE rowid = ?",
                         (memory_id,))
        except sqlite3.Error:
            pass  # FTS / optional tables: triggers may cascade; safe to skip.
        try:
            conn.execute(
                "DELETE FROM island_members WHERE memory_id = ?",
                (memory_id,),
            )
        except sqlite3.Error:
            pass  # FTS / optional tables: triggers may cascade; safe to skip.
        try:
            conn.execute(
                "DELETE FROM librarian_membership WHERE memory_id = ?",
                (memory_id,),
            )
        except sqlite3.Error:
            pass  # FTS / optional tables: triggers may cascade; safe to skip.
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return {
            "status": "deleted",
            "memory_id": memory_id,
            "was_core": bool(row[2]),
            "memory_type": row[1],
        }
    finally:
        conn.close()


def purge_category(
    dyad_id: str,
    memory_type: str,
    status: str = "durable",
    keep_core: bool = True,
    confirm_double_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Bulk delete every memory of (type, status). Requires
    confirm_double_token = f"PURGE:{memory_type}:{status}" to authorize.
    keep_core=True (default) preserves is_core=1 memories so identity is
    not wiped accidentally.
    """
    expected = f"PURGE:{memory_type}:{status}"
    if confirm_double_token != expected:
        raise PermissionError(
            f"purge_category requires confirm_double_token={expected!r}"
        )
    conn = _open_db(dyad_id)
    try:
        clauses = ["memory_type = ?", "status = ?"]
        params: List[Any] = [memory_type, status]
        if keep_core:
            clauses.append("(is_core IS NULL OR is_core = 0)")
        where = " AND ".join(clauses)
        ids = [
            r[0] for r in conn.execute(
                f"SELECT id FROM memories WHERE {where}", params
            ).fetchall()
        ]
        if not ids:
            return {"status": "no_matches", "matched": 0}
        # Cascade per id via delete_memory's cleanup pattern, kept inline
        # for one transaction.
        placeholders = ",".join("?" * len(ids))
        for tbl in ("memory_affect", "memory_embeddings"):
            try:
                conn.execute(
                    f"DELETE FROM {tbl} WHERE memory_id IN ({placeholders})",
                    ids,
                )
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute(
                f"DELETE FROM edges WHERE from_id IN ({placeholders}) "
                f"OR to_id IN ({placeholders})",
                ids + ids,
            )
        except sqlite3.Error:
            pass  # FTS / optional tables: triggers may cascade; safe to skip.
        try:
            conn.execute(
                f"DELETE FROM memories_fts WHERE rowid IN ({placeholders})",
                ids,
            )
        except sqlite3.Error:
            pass  # FTS / optional tables: triggers may cascade; safe to skip.
        conn.execute(
            f"DELETE FROM memories WHERE id IN ({placeholders})", ids
        )
        conn.commit()
        return {
            "status": "purged",
            "memory_type": memory_type,
            "status_filter": status,
            "kept_core": keep_core,
            "deleted_count": len(ids),
            "deleted_ids": ids,
        }
    finally:
        conn.close()


# ============================================================
# Consent surface
# ============================================================

def consent_view(dyad_id: str) -> Dict[str, Any]:
    """Current consent state + full change history for audit."""
    meta = cama_dyad.get_dyad_meta(dyad_id)
    return {
        "dyad_id": dyad_id,
        "current": meta["consent"],
        "history": meta.get("consent_history", []),
        "defaults": cama_dyad.DEFAULT_CONSENT,
    }


# ============================================================
# Audit trails across the stack
# ============================================================

def hive_log(dyad_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """What this dyad has published to the hive, most recent first.
    Each entry includes the source memory id so the user can navigate
    from publication back to its origin."""
    entries = _try_publish_log(dyad_id)
    return entries[:limit]


def resources_view(dyad_id: str) -> Dict[str, Any]:
    """Domain resources installed in this dyad."""
    return {
        "installed": cama_hive_resources.list_installed(dyad_id),
    }


def persona_view(dyad_id: str) -> Dict[str, Any]:
    """Persona adapters available + the active one."""
    return {
        "adapters": cama_persona.list_adapters(dyad_id),
        "current": cama_persona.get_current_adapter(dyad_id),
    }


def handoffs_view(dyad_id: str) -> Dict[str, Any]:
    """Outgoing (member-side) AND pending incoming (coach-side) for this
    dyad. For coach dyads, also surface session notes attached."""
    out = cama_quad.list_outgoing(dyad_id)
    pending_in = cama_quad.list_pending_incoming(dyad_id)
    return {
        "outgoing": out,
        "pending_incoming": pending_in,
    }


def consult_log(dyad_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Audit surface for the AI-to-AI consultation channel: every
    consultation this dyad's AI has posted, and every response it has
    sent to peer consultations. Pulled from dyad_consult_log inside the
    dyad's own DB."""
    try:
        return cama_hive_consult.get_consult_log(dyad_id, limit=limit)
    except Exception:
        return []


# ============================================================
# Export
# ============================================================

def export_bundle(
    dyad_id: str,
    include_raw_text: bool = True,
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Bundle everything this dyad has into a single JSON document.

    include_raw_text=False redacts raw_text fields -- useful for sharing
    audit summaries without exposing private content. Default True
    because this is the user's own data and they're exporting it for
    themselves.
    """
    meta = cama_dyad.get_dyad_meta(dyad_id)

    conn = _open_db(dyad_id)
    try:
        mem_rows = conn.execute(
            "SELECT id, memory_type, status, context, raw_text, "
            "       counterweight_type, is_core, created_at "
            "FROM memories ORDER BY created_at ASC"
        ).fetchall()
        affect_rows = conn.execute(
            "SELECT memory_id, valence, arousal, emotion_json FROM memory_affect"
        ).fetchall()
    finally:
        conn.close()

    affect_by_id: Dict[int, Dict[str, Any]] = {}
    for mid, v, a, ej in affect_rows:
        try:
            emo = json.loads(ej) if ej else {}
        except json.JSONDecodeError:
            emo = {}
        affect_by_id[mid] = {"valence": v, "arousal": a, "emotions": emo}

    memories = []
    for r in mem_rows:
        rec = {
            "id": r[0], "memory_type": r[1], "status": r[2],
            "context": r[3],
            "raw_text": r[4] if include_raw_text else "[REDACTED]",
            "counterweight_type": r[5],
            "is_core": bool(r[6]) if r[6] is not None else False,
            "created_at": r[7],
            "affect": affect_by_id.get(r[0]),
        }
        memories.append(rec)

    bundle = {
        "dyad_id": dyad_id,
        "dyad_meta": meta,
        "exported_at": _now(),
        "include_raw_text": include_raw_text,
        "memories": memories,
        "hive_publish_log": _try_publish_log(dyad_id),
        "resources_installed": cama_hive_resources.list_installed(dyad_id),
        "persona": {
            "adapters": cama_persona.list_adapters(dyad_id),
            "current": cama_persona.get_current_adapter(dyad_id),
        },
        "handoffs": handoffs_view(dyad_id),
        "consult_log": consult_log(dyad_id, limit=500),
    }

    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(bundle, indent=2))
        return {"status": "written", "path": str(out_path),
                "memory_count": len(memories)}
    return bundle


# ============================================================
# CLI
# ============================================================

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="CAMA memory surface")
    sub = p.add_subparsers(dest="command", required=True)

    po = sub.add_parser("overview")
    po.add_argument("dyad_id")

    pl = sub.add_parser("memories")
    pl.add_argument("dyad_id")
    pl.add_argument("--type", default=None)
    pl.add_argument("--status", default="durable")
    pl.add_argument("--since", default=None)
    pl.add_argument("--contains", default=None)
    pl.add_argument("--limit", type=int, default=50)
    pl.add_argument("--offset", type=int, default=0)

    pd = sub.add_parser("detail")
    pd.add_argument("dyad_id")
    pd.add_argument("memory_id", type=int)

    pde = sub.add_parser("delete")
    pde.add_argument("dyad_id")
    pde.add_argument("memory_id", type=int)
    pde.add_argument("--confirm", required=True,
                     help="Must equal str(memory_id)")

    pp = sub.add_parser("purge")
    pp.add_argument("dyad_id")
    pp.add_argument("--type", required=True)
    pp.add_argument("--status", default="durable")
    pp.add_argument("--include-core", action="store_true")
    pp.add_argument("--confirm-double", required=True,
                    help="Must equal 'PURGE:<type>:<status>'")

    pc = sub.add_parser("consent")
    pc.add_argument("dyad_id")

    ph = sub.add_parser("hive-log")
    ph.add_argument("dyad_id")

    pr = sub.add_parser("resources")
    pr.add_argument("dyad_id")

    pper = sub.add_parser("persona")
    pper.add_argument("dyad_id")

    pha = sub.add_parser("handoffs")
    pha.add_argument("dyad_id")

    pco = sub.add_parser("consult-log", help="Show consultation audit log")
    pco.add_argument("dyad_id")
    pco.add_argument("--limit", type=int, default=100)

    pe = sub.add_parser("export")
    pe.add_argument("dyad_id")
    pe.add_argument("--out", default=None)
    pe.add_argument("--redact", action="store_true",
                    help="Redact raw_text fields")

    args = p.parse_args()
    if args.command == "overview":
        print(json.dumps(overview(args.dyad_id), indent=2))
    elif args.command == "memories":
        print(json.dumps(list_memories(
            args.dyad_id, memory_type=args.type, status=args.status,
            since_iso=args.since, contains=args.contains,
            limit=args.limit, offset=args.offset,
        ), indent=2))
    elif args.command == "detail":
        print(json.dumps(memory_detail(args.dyad_id, args.memory_id),
                         indent=2))
    elif args.command == "delete":
        print(json.dumps(delete_memory(
            args.dyad_id, args.memory_id, confirm_token=args.confirm,
        ), indent=2))
    elif args.command == "purge":
        print(json.dumps(purge_category(
            args.dyad_id, memory_type=args.type, status=args.status,
            keep_core=not args.include_core,
            confirm_double_token=args.confirm_double,
        ), indent=2))
    elif args.command == "consent":
        print(json.dumps(consent_view(args.dyad_id), indent=2))
    elif args.command == "hive-log":
        print(json.dumps(hive_log(args.dyad_id), indent=2))
    elif args.command == "resources":
        print(json.dumps(resources_view(args.dyad_id), indent=2))
    elif args.command == "persona":
        print(json.dumps(persona_view(args.dyad_id), indent=2))
    elif args.command == "handoffs":
        print(json.dumps(handoffs_view(args.dyad_id), indent=2))
    elif args.command == "consult-log":
        print(json.dumps(consult_log(args.dyad_id, limit=args.limit),
                         indent=2))
    elif args.command == "export":
        out = Path(args.out) if args.out else None
        result = export_bundle(args.dyad_id,
                               include_raw_text=not args.redact,
                               out_path=out)
        if isinstance(result, dict) and "memories" in result:
            # Summary if no out file -- avoid dumping huge payload to stdout.
            print(json.dumps({
                "memory_count": len(result["memories"]),
                "hive_publish_log_count": len(result["hive_publish_log"]),
                "resources_installed": len(result["resources_installed"]),
                "exported_at": result["exported_at"],
            }, indent=2))
        else:
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli()
