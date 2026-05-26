"""Tests for cama_hive_protocol -- the inter-dyad pattern layer.

Properties under test:
  1. publish refuses without consent.hive_contribute
  2. published records carry no raw text, no names, no dyad_id
  3. dyad_signature rotates across weeks and is stable within a week
  4. k-anonymity blocks queries below threshold
  5. local publish log captures contributions for audit
  6. content from dyad A never appears in the hive ledger as text
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cama.agents import cama_dyad
from cama.hive import cama_hive_protocol as hp


@pytest.fixture(autouse=True)
def isolated_vault_and_hive(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    monkeypatch.setattr(hp, "HIVE_ROOT", tmp_path / "hive")
    yield


def _seed_exchange(
    dyad_id: str,
    text: str,
    valence: float,
    arousal: float,
    emotions: dict,
    context: str = "test_seed",
    when: str = None,
) -> int:
    """Insert an exchange + matching affect row directly into a dyad's DB."""
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(dyad_id)))
    try:
        when = when or datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, confidence, created_at, updated_at) "
            "VALUES (?, 'exchange', ?, 'exchange', 'durable', 'user', 1.0, ?, ?)",
            (text, context, when, when),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO memory_affect "
            "(memory_id, valence, arousal, emotion_json, confidence, computed_at) "
            "VALUES (?, ?, ?, ?, 1.0, ?)",
            (mid, valence, arousal, json.dumps(emotions), when),
        )
        conn.commit()
        return mid
    finally:
        conn.close()


def _opt_in(dyad_id: str) -> None:
    cama_dyad.update_consent(
        dyad_id,
        {"hive_contribute": True},
        reason="test opt-in",
    )


# ============================================================
# Consent
# ============================================================

def test_publish_refused_without_consent():
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    _seed_exchange(r["dyad_id"], "anything", -0.3, 0.4, {"grief": 0.7})
    result = hp.publish_patterns(r["dyad_id"])
    assert result["status"] == "refused"
    assert "hive_contribute" in result["reason"]

    # And the hive ledger has nothing.
    stats = hp.hive_stats()
    assert stats["total_records"] == 0


def test_publish_succeeds_with_consent():
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "anything happened", -0.4, 0.5, {"grief": 0.8})
    result = hp.publish_patterns(r["dyad_id"])
    assert result["status"] == "published"
    assert result["count"] >= 1


# ============================================================
# PII / content stripping
# ============================================================

def test_published_records_carry_no_raw_text():
    r = cama_dyad.init_dyad(person_name="Angela", ai_name="Aurora")
    _opt_in(r["dyad_id"])
    secret = "DELIBERATE_SECRET_PHRASE_THAT_MUST_NOT_LEAK"
    _seed_exchange(r["dyad_id"], f"I told her about {secret}", -0.5, 0.6,
                   {"shame": 0.7, "fear": 0.5})

    res = hp.publish_patterns(r["dyad_id"])
    assert res["status"] == "published"

    # Crack open the ledger DB and confirm the secret is not present anywhere.
    ledger_path = hp.HIVE_ROOT / "ledger.db"
    conn = sqlite3.connect(str(ledger_path))
    try:
        all_text_columns = conn.execute(
            "SELECT * FROM hive_patterns"
        ).fetchall()
        flat = json.dumps(all_text_columns)
        assert secret not in flat, "Raw text leaked into the hive ledger"
        assert "Angela" not in flat
        assert "Aurora" not in flat
        assert r["dyad_id"] not in flat
    finally:
        conn.close()


def test_published_records_have_no_names_or_dyad_id():
    r = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "Bob said something about Brio",
                   0.3, 0.2, {"joy": 0.6})
    hp.publish_patterns(r["dyad_id"])

    # Snapshot the entire ledger contents as a string and assert absence.
    conn = sqlite3.connect(str(hp.HIVE_ROOT / "ledger.db"))
    try:
        rows = conn.execute("SELECT * FROM hive_patterns").fetchall()
        flat = json.dumps(rows)
        for forbidden in ("Bob", "Brio", r["dyad_id"]):
            assert forbidden not in flat, f"{forbidden!r} leaked into hive ledger"
    finally:
        conn.close()


# ============================================================
# Signature rotation
# ============================================================

def test_signature_stable_within_week():
    salt = "ab" * 32  # 64 hex chars = 32 bytes
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    sig1 = hp._dyad_signature(salt, now)
    sig2 = hp._dyad_signature(salt, now + timedelta(days=3))
    assert sig1 == sig2  # same week


def test_signature_rotates_across_weeks():
    salt = "ab" * 32
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    sig_this_week = hp._dyad_signature(salt, now)
    sig_next_week = hp._dyad_signature(salt, now + timedelta(days=8))
    assert sig_this_week != sig_next_week


def test_signatures_diverge_across_dyads():
    sig_a = hp._dyad_signature("aa" * 32)
    sig_b = hp._dyad_signature("bb" * 32)
    assert sig_a != sig_b


# ============================================================
# K-anonymity
# ============================================================

def test_k_anonymity_blocks_small_slice():
    # Make 2 dyads contribute to the same slice; k_threshold=5 should block.
    for name in ("Alice", "Bob"):
        r = cama_dyad.init_dyad(person_name=name, ai_name=name + "AI")
        _opt_in(r["dyad_id"])
        _seed_exchange(r["dyad_id"], "grief about loss",
                       -0.7, 0.6, {"grief": 0.9})
        hp.publish_patterns(r["dyad_id"])

    result = hp.query_policies(
        valence_bucket="very_negative",
        topic_category="loss",
        k_threshold=5,
    )
    assert result["k_anonymity_met"] is False
    assert result["distinct_dyads"] == 2
    assert result["policies"] == []


def test_k_anonymity_passes_with_enough_dyads():
    # 5 distinct dyads, same slice.
    for i in range(5):
        r = cama_dyad.init_dyad(person_name=f"P{i}", ai_name=f"AI{i}")
        _opt_in(r["dyad_id"])
        _seed_exchange(r["dyad_id"], "grief about loss",
                       -0.7, 0.6, {"grief": 0.9})
        hp.publish_patterns(r["dyad_id"])

    result = hp.query_policies(
        valence_bucket="very_negative",
        topic_category="loss",
        k_threshold=5,
    )
    assert result["k_anonymity_met"] is True
    assert result["distinct_dyads"] == 5
    assert result["total_records"] >= 5


# ============================================================
# Dyad-local publish log
# ============================================================

def test_dyad_publish_log_records_what_was_sent():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    mid = _seed_exchange(r["dyad_id"], "feeling something about work",
                         -0.2, 0.3, {"anxiety": 0.6})
    hp.publish_patterns(r["dyad_id"])

    log = hp.get_dyad_publish_log(r["dyad_id"])
    assert len(log) >= 1
    entry = log[0]
    assert entry["source_memory_id"] == mid
    assert entry["topic_category"] in hp.TOPIC_CATEGORIES
    assert entry["valence_bucket"] == "negative"


# ============================================================
# Cross-dyad isolation extends to the hive
# ============================================================

def test_dyad_a_content_never_appears_in_dyad_b_local_log():
    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in(a["dyad_id"])
    _opt_in(b["dyad_id"])

    _seed_exchange(a["dyad_id"], "ALICE_PRIVATE", -0.5, 0.5, {"grief": 0.7})
    hp.publish_patterns(a["dyad_id"])

    # Bob's local log is empty (Bob has not published).
    log_b = hp.get_dyad_publish_log(b["dyad_id"])
    assert log_b == []

    # Bob's vault has no trace of ALICE_PRIVATE.
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(b["dyad_id"])))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE raw_text LIKE '%ALICE_PRIVATE%'"
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


# ============================================================
# Dry-run
# ============================================================

def test_dry_run_writes_nothing():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "anything", 0.1, 0.1, {"calm": 0.5})
    result = hp.publish_patterns(r["dyad_id"], dry_run=True)
    assert result["status"] == "dry_run"
    assert result["would_publish"] >= 1
    assert hp.hive_stats()["total_records"] == 0
    assert hp.get_dyad_publish_log(r["dyad_id"]) == []


# ============================================================
# Bucketing
# ============================================================

@pytest.mark.parametrize("v,expected", [
    (-0.9, "very_negative"),
    (-0.5, "negative"),
    (-0.1, "neutral"),
    (0.0, "neutral"),
    (0.3, "positive"),
    (0.9, "very_positive"),
    (None, "unknown"),
])
def test_bucket_valence(v, expected):
    assert hp._bucket_valence(v) == expected


@pytest.mark.parametrize("a,expected", [
    (-0.5, "calm"),
    (0.0, "steady"),
    (0.5, "activated"),
    (0.9, "high"),
    (None, "unknown"),
])
def test_bucket_arousal(a, expected):
    assert hp._bucket_arousal(a) == expected


@pytest.mark.parametrize("text,expected", [
    ("I keep wanting to die in the morning", "crisis"),
    ("she died last week and I miss her", "loss"),
    ("my therapist increased my medication", "health"),
    ("I'm thrilled about my wedding!", "joy"),
    ("working on the new song tonight", "creative"),
    ("the deadline at work is killing me", "work"),
    ("argument with a friend again", "interpersonal"),
    ("who am I becoming", "identity"),
    ("groceries and laundry", "routine"),
    ("blah blah completely uncategorizable", "other"),
])
def test_topic_abstraction(text, expected):
    assert hp._abstract_topic(text) == expected
