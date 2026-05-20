"""Tests for cama_dyad -- the multi-tenancy foundation.

The properties under test:
  1. init creates a real, isolated dyad DB with the right seed memories
  2. ai_name 'Aelen' (any case) is rejected
  3. two dyads created back-to-back are isolated -- a write in one
     is invisible to the other (the core safety property)
  4. consent updates append history, validate keys, persist to disk
  5. delete requires confirm_token and is real
  6. verify_isolation passes for a healthy dyad and catches a leak
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import cama_dyad


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    """Point cama_dyad at a fresh vault root for every test."""
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    yield


# ============================================================
# init
# ============================================================

def test_init_creates_db_and_meta():
    r = cama_dyad.init_dyad(person_name="Jordan", ai_name="Aurora")

    db_path = Path(r["db_path"])
    meta_path = Path(r["meta_path"])
    assert db_path.exists(), "DB file should be created"
    assert meta_path.exists(), "Metadata file should be created"

    meta = json.loads(meta_path.read_text())
    assert meta["person_name"] == "Jordan"
    assert meta["ai_name"] == "Aurora"
    assert meta["role"] == "person"
    assert meta["consent"]["storage"] is True
    assert meta["consent"]["hive_contribute"] is False
    assert len(meta["consent_history"]) == 1


def test_init_seeds_ai_identity():
    r = cama_dyad.init_dyad(person_name="Sam", ai_name="River")
    conn = sqlite3.connect(r["db_path"])
    try:
        identity_id = r["seed"]["identity_memory_id"]
        row = conn.execute(
            "SELECT raw_text, memory_type, status, context FROM memories WHERE id = ?",
            (identity_id,),
        ).fetchone()
        assert row is not None
        text, mtype, status, context = row
        assert mtype == "teaching"
        assert status == "durable"
        assert "River" in text
        assert "Sam" in text
        assert context == cama_dyad.SEED_CONTEXT_IDENTITY

        # The person gets added to the people table.
        people = conn.execute("SELECT name FROM people").fetchall()
        assert ("Sam",) in people

        # An identity island gets created.
        islands = conn.execute(
            "SELECT name FROM islands WHERE name = ?",
            ("identity_river",),
        ).fetchall()
        assert len(islands) == 1

        # The AI's state KV has ai_name and person_name.
        state = dict(conn.execute(
            "SELECT key, value FROM aelen_state"
        ).fetchall())
        assert state["ai_name"] == "River"
        assert state["person_name"] == "Sam"
    finally:
        conn.close()


def test_init_rejects_reserved_ai_name():
    with pytest.raises(ValueError, match="reserved"):
        cama_dyad.init_dyad(person_name="X", ai_name="Aelen")
    with pytest.raises(ValueError, match="reserved"):
        cama_dyad.init_dyad(person_name="X", ai_name="AELEN")


def test_init_rejects_blank_names():
    with pytest.raises(ValueError):
        cama_dyad.init_dyad(person_name="", ai_name="X")
    with pytest.raises(ValueError):
        cama_dyad.init_dyad(person_name="X", ai_name="   ")


def test_init_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        cama_dyad.init_dyad(person_name="X", ai_name="Y", role="warden")


def test_init_rejects_unknown_consent_key():
    with pytest.raises(ValueError, match="Unknown consent key"):
        cama_dyad.init_dyad(
            person_name="X", ai_name="Y",
            consent={"telepathy": True},
        )


# ============================================================
# Isolation -- the core safety property
# ============================================================

def test_two_dyads_are_isolated():
    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")

    # Different dyad IDs, different DB files.
    assert a["dyad_id"] != b["dyad_id"]
    assert a["db_path"] != b["db_path"]

    # Write a secret into Alice's DB only.
    conn_a = sqlite3.connect(a["db_path"])
    try:
        conn_a.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, status, source_type, proposed_by, "
            " confidence, created_at, updated_at) "
            "VALUES ('ALICE_SECRET_TOKEN', 'exchange', 'durable', 'exchange', "
            "        'user', 1.0, '2026-05-19', '2026-05-19')"
        )
        conn_a.commit()
    finally:
        conn_a.close()

    # Bob's DB cannot see Alice's secret.
    conn_b = sqlite3.connect(b["db_path"])
    try:
        rows = conn_b.execute(
            "SELECT COUNT(*) FROM memories WHERE raw_text = 'ALICE_SECRET_TOKEN'"
        ).fetchone()
        assert rows[0] == 0, (
            "Bob's vault must not contain Alice's data. "
            "Filesystem isolation is the safety boundary."
        )
    finally:
        conn_b.close()


def test_two_dyads_can_share_ai_name():
    # The architecture says: dyad scope is the database file. Two people
    # naming their AI "Aurora" is fine -- they're different AIs.
    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Aurora")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Aurora")
    assert a["dyad_id"] != b["dyad_id"]


# ============================================================
# Consent
# ============================================================

def test_update_consent_appends_history():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    new = cama_dyad.update_consent(
        r["dyad_id"],
        {"hive_contribute": True},
        reason="opted in via settings",
    )
    assert new["hive_contribute"] is True
    assert new["storage"] is True  # unchanged

    meta = cama_dyad.get_dyad_meta(r["dyad_id"])
    assert len(meta["consent_history"]) == 2
    assert meta["consent_history"][-1]["reason"] == "opted in via settings"
    assert meta["consent_history"][-1]["changes"] == {"hive_contribute": True}


def test_update_consent_rejects_unknown_key():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    with pytest.raises(ValueError, match="Unknown consent key"):
        cama_dyad.update_consent(r["dyad_id"], {"telepathy": True})


# ============================================================
# Delete
# ============================================================

def test_delete_requires_confirm_token():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    with pytest.raises(PermissionError):
        cama_dyad.delete_dyad(r["dyad_id"], confirm_token="wrong")
    # Wrong confirm did not delete.
    assert Path(r["db_path"]).exists()


def test_delete_is_real():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    cama_dyad.delete_dyad(r["dyad_id"], confirm_token=r["dyad_id"])
    assert not Path(r["db_path"]).exists()
    assert not Path(r["meta_path"]).exists()
    assert not cama_dyad.dyad_dir(r["dyad_id"]).exists()


def test_delete_missing_raises():
    with pytest.raises(FileNotFoundError):
        cama_dyad.delete_dyad("nonexistent", confirm_token="nonexistent")


# ============================================================
# list / verify
# ============================================================

def test_list_dyads_returns_shallow_metadata_only():
    cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    dyads = cama_dyad.list_dyads()
    assert len(dyads) == 2
    names = {d["person_name"] for d in dyads}
    assert names == {"Alice", "Bob"}
    # The listing surface never includes memory content.
    for d in dyads:
        assert "memories" not in d
        assert "consent" not in d


def test_verify_isolation_passes_for_healthy_dyad():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    result = cama_dyad.verify_isolation(r["dyad_id"])
    assert result["ok"] is True
    assert result["memory_count"] >= 2  # at least identity + bond


def test_verify_detects_reserved_name_leakage():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    # Simulate a leak: a memory that names a reserved AI from another dyad.
    conn = sqlite3.connect(r["db_path"])
    try:
        conn.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, status, source_type, proposed_by, "
            " confidence, created_at, updated_at) "
            "VALUES ('something something Aelen said', 'exchange', 'durable', "
            "        'exchange', 'user', 1.0, '2026-05-19', '2026-05-19')"
        )
        conn.commit()
    finally:
        conn.close()

    result = cama_dyad.verify_isolation(r["dyad_id"])
    assert result["ok"] is False
    assert result["reason"] == "reserved_name_leakage"
