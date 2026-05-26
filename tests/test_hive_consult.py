"""Tests for cama_hive_consult, the AI-to-AI council channel.

Properties under test:
  1. consent gating: post refused without hive_consult; respond refused
     without hive_respond
  2. PII guard catches obvious leaks (email, phone, possessive names,
     dates, length cap)
  3. vocabulary validation: topic_category, counterweight_type,
     trajectory_delta all enforced
  4. pattern-level isolation: ledger never sees dyad_id, only rotating
     signatures; responses never expose responder identity
  5. self-response prevention: source dyad cannot respond to its own
     consultation
  6. quorum mechanism: query_peer_experience and get_responses report
     k_quorum status correctly; aggregate withheld below threshold
  7. lifecycle: resolve / delete / rate restricted to source dyad;
     expiry honored; archive maintenance pass works
  8. audit log: every post and response captured in dyad_consult_log;
     cross-dyad isolation extends to the log
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cama.agents import cama_dyad
from cama.hive import cama_hive_consult as hc
from cama.hive import cama_hive_protocol as hp


@pytest.fixture(autouse=True)
def isolated_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    monkeypatch.setattr(hp, "HIVE_ROOT", tmp_path / "hive")
    yield


def _opt_in_post(dyad_id):
    cama_dyad.update_consent(dyad_id, {"hive_consult": True},
                             reason="test opt-in")


def _opt_in_respond(dyad_id):
    cama_dyad.update_consent(dyad_id, {"hive_respond": True},
                             reason="test opt-in")


# ============================================================
# Consent gating
# ============================================================

def test_post_refused_without_consent():
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    result = hc.post_consultation(
        r["dyad_id"],
        valence_bucket="very_negative",
        arousal_bucket="activated",
        topic_category="loss",
        question="open to peer experience on sustained grief patterns",
    )
    assert result["status"] == "refused"
    assert "hive_consult" in result["reason"]


def test_post_succeeds_with_consent():
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    _opt_in_post(r["dyad_id"])
    result = hc.post_consultation(
        r["dyad_id"],
        valence_bucket="very_negative",
        arousal_bucket="activated",
        topic_category="loss",
        question="open to peer experience on sustained grief patterns",
    )
    assert result["status"] == "posted"
    assert result["consultation_uuid"]
    assert result["signature"]


def test_respond_refused_without_consent():
    poster = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    responder = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in_post(poster["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"],
        valence_bucket="negative",
        arousal_bucket="steady",
        topic_category="work",
        question="counterweight that lands during work plateau",
    )
    result = hc.respond_to_consultation(
        responder["dyad_id"],
        posted["consultation_uuid"],
        have_seen_pattern=True,
        counterweight_type_that_helped="agency",
    )
    assert result["status"] == "refused"
    assert "hive_respond" in result["reason"]


def test_respond_succeeds_with_consent():
    poster = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    responder = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"],
        valence_bucket="negative",
        arousal_bucket="steady",
        topic_category="work",
        question="counterweight that lands during work plateau",
    )
    result = hc.respond_to_consultation(
        responder["dyad_id"],
        posted["consultation_uuid"],
        have_seen_pattern=True,
        counterweight_type_that_helped="agency",
        trajectory_delta="improved",
        rationale_abstract="evidence framing landed when arousal stayed mid",
    )
    assert result["status"] == "responded"


# ============================================================
# PII guard
# ============================================================

@pytest.mark.parametrize("bad_question", [
    "ask jane.doe@example.com for help",
    "patient phone is 555-123-4567",
    "looking at this on 2026-05-20",
    "Maya's pattern keeps recurring",
    "Dr. Smith said this is normal",
    "x" * (hc.MAX_FREE_TEXT + 1),
])
def test_pii_guard_rejects_obvious_leaks(bad_question):
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    result = hc.post_consultation(
        r["dyad_id"],
        valence_bucket="negative",
        arousal_bucket="steady",
        topic_category="health",
        question=bad_question,
    )
    assert result["status"] == "rejected"
    assert "PII" in result["reason"] or "too_long" in result["reason"]


def test_pii_guard_passes_clean_question():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    result = hc.post_consultation(
        r["dyad_id"],
        valence_bucket="negative",
        arousal_bucket="steady",
        topic_category="health",
        question="counterweight types that have landed when affect stays "
                 "high-arousal during sustained health concerns",
    )
    assert result["status"] == "posted"


def test_pii_guard_applies_to_response_rationale():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    responder = cama_dyad.init_dyad(person_name="R", ai_name="RA")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="counterweight ideas for work plateau",
    )
    result = hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True,
        rationale_abstract="contact me at john.smith@example.com",
    )
    assert result["status"] == "rejected"
    assert "PII" in result["reason"]


# ============================================================
# Vocabulary validation
# ============================================================

def test_unknown_topic_category_rejected():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    result = hc.post_consultation(
        r["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="quantum_woo",
        question="any peers seen this signature shape",
    )
    assert result["status"] == "rejected"
    assert "topic_category" in result["reason"]


def test_unknown_counterweight_type_rejected_on_post():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    result = hc.post_consultation(
        r["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="loss",
        question="reviewing counterweight history for sustained grief",
        counterweight_history=[
            {"type": "invented_type", "outcome": "improved"}
        ],
    )
    assert result["status"] == "rejected"
    assert "counterweight" in result["reason"]


def test_unknown_outcome_bucket_rejected_on_post():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    result = hc.post_consultation(
        r["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="loss",
        question="reviewing counterweight history",
        counterweight_history=[
            {"type": "agency", "outcome": "vibed_okay"}
        ],
    )
    assert result["status"] == "rejected"
    assert "outcome" in result["reason"]


def test_unknown_counterweight_rejected_on_respond():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    responder = cama_dyad.init_dyad(person_name="R", ai_name="RA")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="loss",
        question="reviewing peer experience for sustained grief",
    )
    result = hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True,
        counterweight_type_that_helped="alchemy",
    )
    assert result["status"] == "rejected"


# ============================================================
# Pattern-level isolation
# ============================================================

def test_ledger_never_contains_dyad_id_or_names():
    r = cama_dyad.init_dyad(person_name="Maya", ai_name="Solis")
    _opt_in_post(r["dyad_id"])
    hc.post_consultation(
        r["dyad_id"], valence_bucket="very_negative",
        arousal_bucket="activated", topic_category="loss",
        dominant_emotion="grief",
        question="peer experience for sustained grief at this signature",
    )
    ledger = hp.HIVE_ROOT / "ledger.db"
    conn = sqlite3.connect(str(ledger))
    try:
        rows = conn.execute("SELECT * FROM hive_consultations").fetchall()
        flat = json.dumps(rows)
        for forbidden in ["Maya", "Solis", r["dyad_id"]]:
            assert forbidden not in flat, f"{forbidden!r} leaked to ledger"
    finally:
        conn.close()


def test_response_ledger_never_contains_dyad_id_or_names():
    poster = cama_dyad.init_dyad(person_name="Maya", ai_name="Solis")
    responder = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="counterweight selection at this affect shape",
    )
    hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True,
        counterweight_type_that_helped="agency",
        trajectory_delta="improved",
        rationale_abstract="evidence framing tends to land when arousal is mid",
    )
    ledger = hp.HIVE_ROOT / "ledger.db"
    conn = sqlite3.connect(str(ledger))
    try:
        rows = conn.execute(
            "SELECT * FROM hive_consult_responses"
        ).fetchall()
        flat = json.dumps(rows)
        for forbidden in ["Maya", "Solis", "Bob", "Brio",
                          poster["dyad_id"], responder["dyad_id"]]:
            assert forbidden not in flat, f"{forbidden!r} leaked"
    finally:
        conn.close()


# ============================================================
# Self-response prevention
# ============================================================

def test_source_dyad_cannot_respond_to_own_consultation():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    _opt_in_respond(r["dyad_id"])
    posted = hc.post_consultation(
        r["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience with work plateau",
    )
    result = hc.respond_to_consultation(
        r["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True,
    )
    assert result["status"] == "refused"
    assert "source dyad" in result["reason"]


# ============================================================
# Browse
# ============================================================

def test_browse_returns_open_consultations():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    _opt_in_post(poster["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="health",
        question="peer experience at this slice",
    )
    listed = hc.browse_open_consultations()
    uuids = {c["consultation_uuid"] for c in listed}
    assert posted["consultation_uuid"] in uuids


def test_browse_filters_by_topic():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    _opt_in_post(poster["dyad_id"])
    hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="health",
        question="health peer experience",
    )
    hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="loss",
        question="loss peer experience",
    )
    health = hc.browse_open_consultations(topic_category="health")
    loss = hc.browse_open_consultations(topic_category="loss")
    assert all(c["topic_category"] == "health" for c in health)
    assert all(c["topic_category"] == "loss" for c in loss)


def test_browse_excludes_resolved_and_archived():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    _opt_in_post(poster["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience",
    )
    hc.resolve_consultation(poster["dyad_id"], posted["consultation_uuid"])
    listed = hc.browse_open_consultations()
    uuids = {c["consultation_uuid"] for c in listed}
    assert posted["consultation_uuid"] not in uuids


# ============================================================
# Quorum
# ============================================================

def test_get_responses_reports_quorum_status():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    _opt_in_post(poster["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience for work plateau",
    )
    # Two responders, below default k_quorum=3
    for n in range(2):
        r = cama_dyad.init_dyad(person_name=f"R{n}", ai_name=f"RA{n}")
        _opt_in_respond(r["dyad_id"])
        hc.respond_to_consultation(
            r["dyad_id"], posted["consultation_uuid"],
            have_seen_pattern=True,
            counterweight_type_that_helped="agency",
            trajectory_delta="improved",
        )
    res = hc.get_responses(posted["consultation_uuid"], k_quorum=3)
    assert res["quorum_met"] is False
    assert res["distinct_responders"] == 2

    # Add one more responder to hit quorum
    r3 = cama_dyad.init_dyad(person_name="R3", ai_name="RA3")
    _opt_in_respond(r3["dyad_id"])
    hc.respond_to_consultation(
        r3["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True, counterweight_type_that_helped="agency",
    )
    res = hc.get_responses(posted["consultation_uuid"], k_quorum=3)
    assert res["quorum_met"] is True
    assert res["distinct_responders"] == 3


def test_query_peer_experience_below_quorum_returns_no_aggregate():
    # Two posters with the same affect slice, one responder each
    for i in range(2):
        p = cama_dyad.init_dyad(person_name=f"P{i}", ai_name=f"PA{i}")
        _opt_in_post(p["dyad_id"])
        posted = hc.post_consultation(
            p["dyad_id"], valence_bucket="very_negative",
            arousal_bucket="activated", topic_category="loss",
            dominant_emotion="grief",
            question="peer experience for sustained grief",
        )
        r = cama_dyad.init_dyad(person_name=f"R{i}", ai_name=f"RA{i}")
        _opt_in_respond(r["dyad_id"])
        hc.respond_to_consultation(
            r["dyad_id"], posted["consultation_uuid"],
            have_seen_pattern=True,
            counterweight_type_that_helped="self_compassion",
            trajectory_delta="improved",
        )

    res = hc.query_peer_experience(
        valence_bucket="very_negative", arousal_bucket="activated",
        topic_category="loss", dominant_emotion="grief", k_quorum=3,
    )
    assert res["k_quorum_met"] is False
    assert res["distinct_responders"] == 2


def test_query_peer_experience_above_quorum_returns_aggregate():
    # Three posters, three distinct responders, all reporting same affect slice
    for i in range(3):
        p = cama_dyad.init_dyad(person_name=f"P{i}", ai_name=f"PA{i}")
        _opt_in_post(p["dyad_id"])
        posted = hc.post_consultation(
            p["dyad_id"], valence_bucket="very_negative",
            arousal_bucket="activated", topic_category="loss",
            dominant_emotion="grief",
            question=f"slot {i} peer experience for sustained grief",
        )
        r = cama_dyad.init_dyad(person_name=f"R{i}", ai_name=f"RA{i}")
        _opt_in_respond(r["dyad_id"])
        hc.respond_to_consultation(
            r["dyad_id"], posted["consultation_uuid"],
            have_seen_pattern=True,
            counterweight_type_that_helped="self_compassion",
            trajectory_delta="improved",
        )

    res = hc.query_peer_experience(
        valence_bucket="very_negative", arousal_bucket="activated",
        topic_category="loss", dominant_emotion="grief", k_quorum=3,
    )
    assert res["k_quorum_met"] is True
    assert res["distinct_responders"] == 3
    assert res["counterweight_vote_distribution"]["self_compassion"] == 3
    assert res["improvements_by_counterweight"]["self_compassion"] == 3


# ============================================================
# Lifecycle: resolve / delete / rate / expire
# ============================================================

def test_only_source_dyad_can_resolve():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    other = cama_dyad.init_dyad(person_name="O", ai_name="OA")
    _opt_in_post(poster["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience for work plateau",
    )
    bad = hc.resolve_consultation(other["dyad_id"],
                                   posted["consultation_uuid"])
    assert bad["status"] == "refused"
    ok = hc.resolve_consultation(poster["dyad_id"],
                                  posted["consultation_uuid"])
    assert ok["status"] == "resolved"


def test_delete_cascades_to_responses():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    responder = cama_dyad.init_dyad(person_name="R", ai_name="RA")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience",
    )
    hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True, counterweight_type_that_helped="agency",
    )
    # Delete by source dyad
    res = hc.delete_consultation(poster["dyad_id"],
                                  posted["consultation_uuid"])
    assert res["status"] == "deleted"
    # Confirm responses are gone too
    ledger = hp.HIVE_ROOT / "ledger.db"
    conn = sqlite3.connect(str(ledger))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM hive_consult_responses"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_rate_response_restricted_to_source_dyad():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    responder = cama_dyad.init_dyad(person_name="R", ai_name="RA")
    intruder = cama_dyad.init_dyad(person_name="I", ai_name="IA")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience",
    )
    resp = hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True, counterweight_type_that_helped="agency",
    )
    bad = hc.rate_response(intruder["dyad_id"], resp["response_uuid"],
                            helpfulness=1)
    assert bad["status"] == "refused"
    ok = hc.rate_response(poster["dyad_id"], resp["response_uuid"],
                           helpfulness=1)
    assert ok["status"] == "rated"


def test_expired_consultation_blocks_response():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    responder = cama_dyad.init_dyad(person_name="R", ai_name="RA")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience", expiry_days=0,
    )
    result = hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True,
    )
    assert result["status"] == "consultation_expired"


def test_expire_old_consultations_archives_them():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    _opt_in_post(poster["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience", expiry_days=0,
    )
    result = hc.expire_old_consultations()
    assert result["archived_count"] >= 1
    # Browse should no longer return it
    listed = hc.browse_open_consultations()
    uuids = {c["consultation_uuid"] for c in listed}
    assert posted["consultation_uuid"] not in uuids


# ============================================================
# Audit log
# ============================================================

def test_post_records_local_audit():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    posted = hc.post_consultation(
        r["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="loss",
        question="peer experience for sustained grief",
    )
    log = hc.get_consult_log(r["dyad_id"])
    assert len(log) >= 1
    entry = log[0]
    assert entry["direction"] == "posted"
    assert entry["consultation_uuid"] == posted["consultation_uuid"]
    assert "peer experience" in entry["payload"]["question"]


def test_respond_records_local_audit():
    poster = cama_dyad.init_dyad(person_name="P", ai_name="PA")
    responder = cama_dyad.init_dyad(person_name="R", ai_name="RA")
    _opt_in_post(poster["dyad_id"])
    _opt_in_respond(responder["dyad_id"])
    posted = hc.post_consultation(
        poster["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience",
    )
    hc.respond_to_consultation(
        responder["dyad_id"], posted["consultation_uuid"],
        have_seen_pattern=True, counterweight_type_that_helped="agency",
        rationale_abstract="evidence framing landed",
    )
    log = hc.get_consult_log(responder["dyad_id"])
    assert any(e["direction"] == "responded" for e in log)


def test_dyad_a_post_does_not_appear_in_dyad_b_log():
    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in_post(a["dyad_id"])
    hc.post_consultation(
        a["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="loss",
        question="peer experience for sustained grief",
    )
    log_b = hc.get_consult_log(b["dyad_id"])
    assert log_b == []


# ============================================================
# Signature rotation
# ============================================================

def test_signature_matches_hive_protocol_scheme():
    """The consult channel reuses the same rotating signature as
    hive_protocol so that the same dyad has the same signature in both
    channels within a week (useful for cross-channel quorum work later)
    and a different one across weeks (privacy)."""
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_post(r["dyad_id"])
    posted = hc.post_consultation(
        r["dyad_id"], valence_bucket="negative",
        arousal_bucket="steady", topic_category="work",
        question="peer experience",
    )
    meta = cama_dyad.get_dyad_meta(r["dyad_id"])
    expected = hp._dyad_signature(meta["hive_signing_salt"])
    assert posted["signature"] == expected
