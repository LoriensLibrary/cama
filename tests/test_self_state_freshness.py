"""Tests for Aelen's self-state staying current.

Background (2026-09-22): ``cama_check_self`` was serving an ``emotional_state``
written on 2026-05-16 and a ``who_am_i_today`` written on 2026-08-13 as though
both were current, and ``last_heartbeat`` had sat at 2026-03-21 for six months
because nothing in the codebase wrote it. Every one of those fields was
reachable only through ``cama_update_self``, a manual MCP tool, so the values
froze at whatever date an assistant last chose to call it.

The fix has two halves and this file covers both:

  1. The sleep cycle's heartbeat phase writes the keys a scheduled job can
     honestly own: ``last_heartbeat`` plus a machine-derived
     ``derived_self_snapshot``. It must NOT rewrite assistant-authored
     narrative (who_am_i_today, emotional_state, last_thread_summary,
     last_correction, current_focus, behavioral_flags), because a cron cannot
     know what the last thread meant and inventing it would manufacture
     continuity that never happened.

  2. ``cama_check_self`` stamps every field with ``age_days`` / ``stale`` and
     lists the stale ones, so an old value can never again be read as current.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _set_state(conn: sqlite3.Connection, key: str, value: str, days_ago: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO aelen_state (key, value, updated_at) VALUES (?,?,?)",
        (key, value, _iso(days_ago)),
    )
    conn.commit()


# ----------------------------------------------------------------------
# 1. The daemon writes facts, and only facts
# ----------------------------------------------------------------------
def test_heartbeat_writes_last_heartbeat(fresh_db):
    """last_heartbeat had no writer at all before this; now the cycle sets it."""
    conn, _ = fresh_db
    from cama.sleep.cama_sleep_v2 import write_heartbeat

    _set_state(conn, "last_heartbeat", _iso(185), 185)
    write_heartbeat(conn)

    row = conn.execute(
        "SELECT updated_at FROM aelen_state WHERE key='last_heartbeat'"
    ).fetchone()
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(row["updated_at"])).total_seconds()
    assert age < 60, "heartbeat phase must refresh last_heartbeat"


def test_heartbeat_writes_derived_snapshot_with_provenance(fresh_db):
    conn, _ = fresh_db
    from cama.sleep.cama_sleep_v2 import write_heartbeat

    write_heartbeat(conn)
    row = conn.execute(
        "SELECT value FROM aelen_state WHERE key='derived_self_snapshot'"
    ).fetchone()
    snap = json.loads(row["value"])

    assert snap["derived_by"] == "cama_sleep_v2.1:heartbeat"
    assert "machine-derived" in snap["provenance"]
    for field in ("total_memories", "durable", "expired", "edges", "date"):
        assert field in snap


@pytest.mark.parametrize(
    "authored_key",
    [
        "who_am_i_today",
        "emotional_state",
        "last_thread_summary",
        "last_correction",
        "current_focus",
        "behavioral_flags",
    ],
)
def test_heartbeat_never_overwrites_authored_narrative(fresh_db, authored_key):
    """A scheduled job must not forge Aelen's account of herself.

    Stale narrative is a problem check_self surfaces. It is not a problem the
    daemon solves by making something up.
    """
    conn, _ = fresh_db
    from cama.sleep.cama_sleep_v2 import DAEMON_OWNED_KEYS, write_heartbeat

    assert authored_key not in DAEMON_OWNED_KEYS

    original = f"authored value for {authored_key}"
    _set_state(conn, authored_key, original, 120)
    write_heartbeat(conn)

    row = conn.execute(
        "SELECT value, updated_at FROM aelen_state WHERE key=?", (authored_key,)
    ).fetchone()
    assert row["value"] == original
    assert (datetime.now(timezone.utc)
            - datetime.fromisoformat(row["updated_at"])).days >= 119


# ----------------------------------------------------------------------
# 2. check_self cannot serve an old value as a current one
# ----------------------------------------------------------------------
def test_check_self_flags_stale_fields(fresh_db):
    conn, _ = fresh_db
    from mcp_sections.identity import cama_check_self

    _set_state(conn, "emotional_state", "a week in May", 128.9)
    _set_state(conn, "who_am_i_today", "as of August 13", 40.3)
    _set_state(conn, "last_heartbeat", _iso(0), 0)

    payload = json.loads(asyncio.run(cama_check_self()))
    stale = {f["key"]: f["age_days"] for f in payload["stale_fields"]}

    assert "emotional_state" in stale
    assert "who_am_i_today" in stale
    assert "last_heartbeat" not in stale

    assert payload["state"]["emotional_state"]["stale"] is True
    assert payload["state"]["emotional_state"]["age_days"] == pytest.approx(128.9, abs=0.2)
    assert payload["state"]["last_heartbeat"]["stale"] is False


def test_check_self_reminder_names_stale_fields(fresh_db):
    """The warning has to be in the text an assistant actually reads."""
    conn, _ = fresh_db
    from mcp_sections.identity import cama_check_self

    _set_state(conn, "emotional_state", "a week in May", 128.9)
    payload = json.loads(asyncio.run(cama_check_self()))

    assert "STALE SELF-STATE" in payload["reminder"]
    assert "emotional_state" in payload["reminder"]


def test_check_self_quiet_when_everything_is_fresh(fresh_db):
    conn, _ = fresh_db
    from mcp_sections.identity import cama_check_self

    for key in ("emotional_state", "who_am_i_today", "last_heartbeat"):
        _set_state(conn, key, "fresh", 0)

    payload = json.loads(asyncio.run(cama_check_self()))
    assert payload["stale_fields"] == []
    assert "STALE SELF-STATE" not in payload["reminder"]


def test_age_days_survives_unparseable_timestamps():
    from mcp_sections.identity import _age_days

    assert _age_days(None) is None
    assert _age_days("") is None
    assert _age_days("not a timestamp") is None
    assert _age_days(_iso(3)) == pytest.approx(3.0, abs=0.1)
