#!/usr/bin/env python3
"""
CAMA Scent, cama_scent.py
The boot-side read path for the hive's pheromone channel.

Why this module exists
----------------------
cama_hive.read_pheromones() has carried this docstring since April 2026:

    "This is what the new thread 'smells' on boot."

It was not what the new thread smelled on boot. Between 2026-04-03 and
2026-04-30 the hive emitted 50 pheromones and consumed_count stayed at 0 for
every one of them, because nothing on the boot path ever read the channel.
cama_hive.enrich_boot() was written for exactly that job and its own
docstring says it is "called during cama_thread_start". The April 3 snapshot
of the MCP server did call it. When the server was refactored into
mcp_sections/, the eight-line hive block was dropped and never carried
across, so the channel has had a writer and no consumer ever since.

That is a regression, and restoring the call is most of the fix. This module
supplies the rest, which is what a broadcast channel needs before it is safe
to inject into every new thread:

  1. ROUTING. A finding is published with the librarian routes it belongs to,
     and a booting thread receives on the routes it actually activated rather
     than on everything currently alive. Broadcast to every thread is
     quadratic in messages and mostly noise. Associative multicast is
     neither, and it reuses routing the boot has already computed.

  2. RECTIFICATION. Score is decayed intensity times route overlap,
     thresholded, and clamped to zero. Signals under threshold never reach
     the payload. SCENT_LEAK keeps an off-route or unrouted finding at a
     reduced but nonzero gain, so a quiet route cannot go permanently deaf.
     A hard rectifier would introduce the dead-unit failure: a leaf that
     stopped clearing threshold would never route again, and its findings
     would become permanently unreachable.

  3. A RECEIPT. emit_finding() refuses a finding with no evidence behind it.
     The failure this guards against is observed, not hypothetical: a thread
     inferred from a run of failed calls that the memory system was
     unreachable, and was wrong, because the transport was misconfigured
     while a second server answered normally. A channel carrying conclusions
     would have published that belief to every thread at once. A channel
     carrying evidence makes a wrong finding inspectable, and leaves
     hive_stops able to suppress it.

Design constraint borrowed from the biology this layer already models: real
queen pheromone carries no propositions. It modulates how the receiver
weights reward and threat. Scent keeps that property. It changes what a
booting thread finds salient. It does not tell the thread what is true.

Part of the CAMA (Circular Associative Memory Architecture) system.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

from cama.core.time_utils import now_iso as _now
from cama.hive import cama_hive

logger = logging.getLogger(__name__)

# ============================================================
# Rectifier parameters
# ============================================================
# Worked arithmetic, so these stay tunable with intent rather than by feel:
#   fully on-route (overlap 1.0) passes when intensity >= 0.15
#   off-route or unrouted (gain = SCENT_LEAK) passes when intensity >= 0.43
# A weak finding on the wrong route dies. A strong one still gets through at
# reduced weight. Raise SCENT_THRESHOLD to sharpen; raise SCENT_LEAK to make
# the channel more permissive toward unrouted history.
SCENT_THRESHOLD = 0.15
SCENT_LEAK = 0.35
SCENT_MAX = 5

VALID_KINDS = ("finding", "person_state")


def _ensure_table(c: sqlite3.Connection) -> None:
    """Create the scent sidecar. Additive only, never alters hive_pheromones."""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS hive_scent (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pheromone_id  INTEGER NOT NULL,
            routes        TEXT    NOT NULL DEFAULT '[]',
            receipt       TEXT    NOT NULL,
            kind          TEXT    NOT NULL DEFAULT 'finding',
            created_at    TEXT    NOT NULL
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_hive_scent_pheromone "
        "ON hive_scent(pheromone_id)"
    )


def _overlap(
    finding_routes: Sequence[str],
    active_routes: Sequence[str],
) -> Optional[float]:
    """Fraction of a finding's routes that the booting thread activated.

    Returns None when overlap is undefined, which the rectifier treats as the
    leak path rather than as a zero. An unrouted finding is unclassified, not
    irrelevant, and that distinction covers every pheromone emitted before
    this module existed.
    """
    if not finding_routes or not active_routes:
        return None
    fr = {str(r).strip().lower() for r in finding_routes if str(r).strip()}
    ar = {str(r).strip().lower() for r in active_routes if str(r).strip()}
    if not fr or not ar:
        return None
    return len(fr & ar) / len(fr)


def rectify(
    intensity: float,
    finding_routes: Sequence[str],
    active_routes: Sequence[str],
) -> float:
    """Leaky-rectified salience for one finding against one thread's routes.

    Zero means the finding does not reach the boot payload at all.
    """
    ov = _overlap(finding_routes, active_routes)
    gain = SCENT_LEAK if (ov is None or ov <= 0.0) else ov
    score = max(0.0, float(intensity)) * gain
    return round(score, 4) if score >= SCENT_THRESHOLD else 0.0


def emit_finding(
    signal: str,
    receipt: str,
    routes: Optional[List[str]] = None,
    kind: str = "finding",
    intensity: float = 0.6,
    source_thread: Optional[str] = None,
    duration_hours: float = cama_hive.PHEROMONE_DECAY_HOURS,
) -> Dict[str, Any]:
    """Publish a routed finding onto the hive channel.

    A receipt is mandatory. A caller holding only a conclusion does not get to
    publish it, which is the whole point of the gate.
    """
    if not signal or not signal.strip():
        return {"status": "rejected", "reason": "signal is empty"}
    if not receipt or not receipt.strip():
        return {
            "status": "rejected",
            "reason": (
                "a finding needs a receipt: the command output, count, id or "
                "timestamp that makes it checkable. Publish evidence, not a "
                "conclusion."
            ),
        }
    if kind not in VALID_KINDS:
        return {"status": "rejected", "reason": "kind must be one of %s" % (VALID_KINDS,)}

    ph_type = "discovery" if kind == "finding" else "attention_weight"
    emitted = cama_hive.emit_pheromone(
        pheromone_type=ph_type,
        signal=signal.strip(),
        intensity=min(1.0, max(0.0, float(intensity))),
        source_thread=source_thread,
        source_context=receipt.strip()[:500],
        duration_hours=duration_hours,
    )
    pid = emitted.get("pheromone_id")

    c = cama_hive.get_db()
    try:
        _ensure_table(c)
        c.execute(
            "INSERT INTO hive_scent (pheromone_id, routes, receipt, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, json.dumps(list(routes or [])), receipt.strip(), kind, _now()),
        )
        c.commit()
    finally:
        c.close()

    return {
        "status": "emitted",
        "pheromone_id": pid,
        "kind": kind,
        "routes": list(routes or []),
        "expires_at": emitted.get("expires_at"),
    }


def read_scent(
    active_routes: Optional[List[str]] = None,
    limit: int = SCENT_MAX,
) -> List[Dict[str, Any]]:
    """What this thread smells on boot, rectified against its own routes.

    Increments consumed_count on everything actually returned. That column has
    existed since April and was never written by anything, which is why the
    channel could sit dead for months without the silence being visible.
    """
    active_routes = list(active_routes or [])
    try:
        live = cama_hive.read_pheromones()
    except Exception as err:
        logger.warning("[CAMA scent] could not read pheromones: %s", err)
        return []
    if not live:
        return []

    c = cama_hive.get_db()
    try:
        _ensure_table(c)
        meta: Dict[int, Dict[str, Any]] = {}
        for r in c.execute(
            "SELECT pheromone_id, routes, receipt, kind FROM hive_scent"
        ).fetchall():
            try:
                rts = json.loads(r["routes"])
            except Exception:
                rts = []
            meta[r["pheromone_id"]] = {
                "routes": rts,
                "receipt": r["receipt"],
                "kind": r["kind"],
            }

        scored: List[Dict[str, Any]] = []
        for p in live:
            pid = p.get("id")
            m = meta.get(pid, {})
            score = rectify(
                p.get("current_intensity", p.get("intensity", 0.0)),
                m.get("routes", []),
                active_routes,
            )
            if score <= 0.0:
                continue
            scored.append({
                "id": pid,
                "kind": m.get("kind", "legacy"),
                "type": p.get("pheromone_type", p.get("type")),
                "signal": p.get("signal"),
                "salience": score,
                "routes": m.get("routes", []),
                "receipt": m.get("receipt") or p.get("source_context"),
                "source_thread": p.get("source_thread"),
            })

        scored.sort(key=lambda x: x["salience"], reverse=True)
        out = scored[:limit]

        if out:
            c.executemany(
                "UPDATE hive_pheromones SET consumed_count = consumed_count + 1 "
                "WHERE id = ?",
                [(s["id"],) for s in out],
            )
            c.commit()
        return out
    finally:
        c.close()


def register(mcp) -> None:
    """Expose the scent channel as MCP tools."""

    @mcp.tool()
    def cama_emit_finding(
        signal: str,
        receipt: str,
        routes: Optional[List[str]] = None,
        kind: str = "finding",
        intensity: float = 0.6,
        source_thread: Optional[str] = None,
    ) -> str:
        """Publish a finding that other threads should smell on boot.

        signal: the finding, stated plainly and briefly.
        receipt: the evidence that makes it checkable. Required.
        routes: librarian routes this belongs to, e.g. ["topic_cama"].
        kind: "finding" for a fact about the world, "person_state" for a
              time-bounded observation about the human. Record an observation
              with a timestamp, never an inference about their interior.
        """
        return json.dumps(
            emit_finding(
                signal=signal,
                receipt=receipt,
                routes=routes,
                kind=kind,
                intensity=intensity,
                source_thread=source_thread,
            ),
            indent=2,
            default=str,
        )

    @mcp.tool()
    def cama_read_scent(
        active_routes: Optional[List[str]] = None,
        limit: int = SCENT_MAX,
    ) -> str:
        """Read findings routed to this thread, rectified and decayed."""
        return json.dumps(
            {"scent": read_scent(active_routes=active_routes, limit=limit)},
            indent=2,
            default=str,
        )
