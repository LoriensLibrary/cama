"""Tests for cama_quad -- the coach/member handoff layer.

Properties under test:
  1. Mutual consent gating: both consent.coach_handoff (member) and
     consent.receive_handoffs (coach) must be True.
  2. Even with consent on, every handoff requires the member's
     per-instance authorization (member_authorization=True). Blanket
     consent does NOT authorize specific transfers.
  3. The brief is pattern-level by default -- no raw exchange text
     unless the member explicitly attaches a memory_id.
  4. The coach must be role='coach' on the target dyad.
  5. Brief is byte-identical on both sides; SHA-256 fingerprint allows
     either side to prove what was sent.
  6. Member can revoke before read; coach-side files vanish.
  7. After read, revocation is recorded but the coach's copy remains
     (clinical reality).
  8. Reading requires coach's per-instance authorization.
  9. Cross-dyad isolation: a third dyad's view sees nothing.
  10. Expiry honors the configured deadline.
  11. Session notes flow back to the member's audit trail.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from cama.agents import cama_dyad
from cama.agents import cama_quad
@pytest.fixture(autouse=True)
def isolated_vaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    yield


def _seed_exchange(
    dyad_id: str, raw: str, valence: float = 0.0, arousal: float = 0.0,
    emotions: dict = None, days_ago: int = 0,
) -> int:
    """Seed an exchange that may have a created_at in the past."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(dyad_id)))
    try:
        cur = conn.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, confidence, created_at, updated_at) "
            "VALUES (?, 'exchange', 'seed', 'exchange', 'durable', "
            "        'user', 1.0, ?, ?)",
            (raw, ts, ts),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO memory_affect "
            "(memory_id, valence, arousal, emotion_json, confidence, "
            " computed_at) "
            "VALUES (?, ?, ?, ?, 1.0, ?)",
            (mid, valence, arousal,
             json.dumps(emotions or {}), ts),
        )
        conn.commit()
        return mid
    finally:
        conn.close()


def _make_member(name: str = "Maya"):
    r = cama_dyad.init_dyad(person_name=name, ai_name=name + "AI")
    cama_dyad.update_consent(r["dyad_id"], {"coach_handoff": True})
    return r


def _make_coach(name: str = "Dani"):
    r = cama_dyad.init_dyad(person_name=name, ai_name=name + "AI", role="coach")
    cama_dyad.update_consent(r["dyad_id"], {"receive_handoffs": True})
    return r


# ============================================================
# Consent gating
# ============================================================

def test_handoff_requires_member_authorization():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"],
        member_authorization=False,
    )
    assert res["status"] == "refused"
    assert "member_authorization" in res["reason"]


def test_handoff_requires_member_consent_flag():
    m = cama_dyad.init_dyad(person_name="Maya", ai_name="Solis")
    # Note: coach_handoff NOT enabled
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    assert res["status"] == "refused"
    assert "coach_handoff" in res["reason"]


def test_handoff_requires_coach_consent_flag():
    m = _make_member()
    c = cama_dyad.init_dyad(person_name="Dani", ai_name="Halo", role="coach")
    # Note: receive_handoffs NOT enabled
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    assert res["status"] == "refused"
    assert "receive_handoffs" in res["reason"]


def test_handoff_rejects_non_coach_target():
    m = _make_member()
    # Target dyad has role='person', not 'coach'.
    target = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    cama_dyad.update_consent(target["dyad_id"], {"receive_handoffs": True})
    res = cama_quad.initiate_handoff(
        m["dyad_id"], target["dyad_id"], member_authorization=True,
    )
    assert res["status"] == "refused"
    assert "role" in res["reason"]


# ============================================================
# Successful handoff + content properties
# ============================================================

def test_handoff_delivered_writes_both_sides():
    m = _make_member()
    c = _make_coach()
    _seed_exchange(m["dyad_id"], "[USER] tough week\n[ASSISTANT] I know",
                   valence=-0.5, arousal=0.4,
                   emotions={"grief": 0.6})

    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    assert res["status"] == "delivered"
    h = res["handoff_id"]

    member_d = cama_quad._outgoing_dir(m["dyad_id"], h)
    coach_d = cama_quad._incoming_dir(c["dyad_id"], h)
    assert (member_d / "brief.json").exists()
    assert (coach_d / "brief.json").exists()
    # Byte-identical briefs.
    assert (member_d / "brief.json").read_text() == \
           (coach_d / "brief.json").read_text()


def test_brief_carries_no_raw_exchange_text_by_default():
    m = _make_member()
    c = _make_coach()
    secret = "PRIVATE_EXCHANGE_TEXT_THAT_MUST_NOT_LEAK"
    _seed_exchange(m["dyad_id"], f"[USER] {secret}\n[ASSISTANT] reply",
                   valence=-0.3, arousal=0.5,
                   emotions={"anxiety": 0.6})

    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]
    brief_text = (
        cama_quad._incoming_dir(c["dyad_id"], h) / "brief.json"
    ).read_text()
    assert secret not in brief_text


def test_explicit_shares_allow_intentional_content():
    m = _make_member()
    c = _make_coach()
    mid = _seed_exchange(
        m["dyad_id"], "[USER] q\n[ASSISTANT] MEMBER_EXPLICITLY_SHARED",
    )
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
        explicit_shares=[{"memory_id": mid, "reason": "context for coach"}],
    )
    h = res["handoff_id"]
    brief_text = (
        cama_quad._incoming_dir(c["dyad_id"], h) / "brief.json"
    ).read_text()
    # Only because the member explicitly opted that memory in.
    assert "MEMBER_EXPLICITLY_SHARED" in brief_text


def test_brief_includes_affect_trend_and_themes():
    m = _make_member()
    c = _make_coach()
    _seed_exchange(m["dyad_id"], "[USER] grief about loss\n[ASSISTANT] ok",
                   valence=-0.7, arousal=0.5,
                   emotions={"grief": 0.9}, days_ago=1)
    _seed_exchange(m["dyad_id"], "[USER] working on the new song tonight\n"
                                  "[ASSISTANT] sounds beautiful",
                   valence=0.4, arousal=0.3,
                   emotions={"joy": 0.6}, days_ago=2)

    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    brief = json.loads(
        (cama_quad._incoming_dir(c["dyad_id"], res["handoff_id"])
         / "brief.json").read_text()
    )
    assert "affect_trend" in brief
    assert any(d["exchange_count"] >= 1 for d in brief["affect_trend"])
    assert "active_themes" in brief
    topics = {t["topic_category"] for t in brief["active_themes"]}
    assert "loss" in topics or "creative" in topics


# ============================================================
# Read flow
# ============================================================

def test_read_requires_coach_authorization():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]
    read_res = cama_quad.read_handoff(c["dyad_id"], h,
                                      coach_authorization=False)
    assert read_res["status"] == "refused"


def test_read_marks_both_sides():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]
    cama_quad.read_handoff(c["dyad_id"], h, coach_authorization=True)
    # Coach manifest marked read.
    coach_m = json.loads(
        (cama_quad._incoming_dir(c["dyad_id"], h) / "manifest.json")
        .read_text()
    )
    assert coach_m["read_at"] is not None
    # Member manifest mirrored.
    member_m = json.loads(
        (cama_quad._outgoing_dir(m["dyad_id"], h) / "manifest.json")
        .read_text()
    )
    assert member_m["read_at"] == coach_m["read_at"]
    assert member_m["status"] == "read"


def test_pending_incoming_excludes_read_and_revoked():
    m = _make_member()
    c = _make_coach()
    res1 = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    res2 = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    # Read res1.
    cama_quad.read_handoff(c["dyad_id"], res1["handoff_id"],
                           coach_authorization=True)
    pending = cama_quad.list_pending_incoming(c["dyad_id"])
    ids = {p["handoff_id"] for p in pending}
    assert res1["handoff_id"] not in ids
    assert res2["handoff_id"] in ids


# ============================================================
# Revocation
# ============================================================

def test_revoke_before_read_deletes_coach_copy():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]
    coach_d = cama_quad._incoming_dir(c["dyad_id"], h)
    assert coach_d.exists()

    rv = cama_quad.revoke_handoff(m["dyad_id"], h)
    assert rv["status"] == "revoked"
    assert rv["coach_already_read"] is False
    assert not coach_d.exists()


def test_revoke_after_read_keeps_coach_copy_but_records_revocation():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]
    cama_quad.read_handoff(c["dyad_id"], h, coach_authorization=True)

    rv = cama_quad.revoke_handoff(m["dyad_id"], h)
    assert rv["status"] == "revoked_after_read"
    assert rv["coach_already_read"] is True
    # Coach copy persists.
    assert cama_quad._incoming_dir(c["dyad_id"], h).exists()
    # Member side records the revocation.
    mm = json.loads(
        (cama_quad._outgoing_dir(m["dyad_id"], h) / "manifest.json")
        .read_text()
    )
    assert mm["revoked_at"] is not None


# ============================================================
# Session notes
# ============================================================

def test_session_note_requires_authorization_and_prior_read():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]

    # Without read first.
    rn = cama_quad.add_session_note(
        c["dyad_id"], h, note={"text": "x"}, coach_authorization=True,
    )
    assert rn["status"] == "refused"

    # With read but no authorization.
    cama_quad.read_handoff(c["dyad_id"], h, coach_authorization=True)
    rn = cama_quad.add_session_note(
        c["dyad_id"], h, note={"text": "x"}, coach_authorization=False,
    )
    assert rn["status"] == "refused"


def test_session_note_mirrors_to_member():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    h = res["handoff_id"]
    cama_quad.read_handoff(c["dyad_id"], h, coach_authorization=True)
    cama_quad.add_session_note(
        c["dyad_id"], h, note={"summary": "next session in 1 week"},
        coach_authorization=True,
    )

    # Member side has the note.
    member_note = (cama_quad._outgoing_dir(m["dyad_id"], h)
                   / "session_note.json")
    assert member_note.exists()
    note = json.loads(member_note.read_text())
    assert note["summary"] == "next session in 1 week"


# ============================================================
# Cross-dyad isolation
# ============================================================

def test_third_dyad_sees_nothing():
    m = _make_member()
    c = _make_coach()
    other = _make_coach("Wren")  # another coach -- irrelevant party

    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )

    # The third coach has no incoming handoffs and no insight into this one.
    pending = cama_quad.list_pending_incoming(other["dyad_id"])
    assert pending == []
    brief = cama_quad.get_brief_for_session(other["dyad_id"], m["dyad_id"])
    assert brief is None


# ============================================================
# Brief retrieval for a session
# ============================================================

def test_get_brief_for_session_returns_most_recent():
    m = _make_member()
    c = _make_coach()
    cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
        purpose="first",
    )
    second = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
        purpose="second",
    )

    brief = cama_quad.get_brief_for_session(c["dyad_id"], m["dyad_id"])
    assert brief["manifest"]["handoff_id"] == second["handoff_id"]
    assert brief["manifest"]["purpose"] == "second"


# ============================================================
# Expiry
# ============================================================

def test_expiry_blocks_read():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
        expires_in_hours=0,  # immediately expires
    )
    h = res["handoff_id"]
    read_res = cama_quad.read_handoff(c["dyad_id"], h,
                                      coach_authorization=True)
    assert read_res["status"] == "expired"


def test_expire_old_handoffs_removes_them():
    m = _make_member()
    c = _make_coach()
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
        expires_in_hours=0,
    )
    h = res["handoff_id"]
    result = cama_quad.expire_old_handoffs(m["dyad_id"])
    assert h in result["removed_ids"]
    assert not cama_quad._outgoing_dir(m["dyad_id"], h).exists()
