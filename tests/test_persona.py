"""Tests for cama_persona -- the persona scaffolding.

Properties under test:
  1. consent.persona_training gates every write path
  2. prepare_adapter exports exchanges + identity pins into a versioned dir
  3. fingerprints match what was written; verify_adapter catches tampering
  4. identity pins are present and non-empty for any prepared adapter
  5. set_current / get_current / rollback work as expected
  6. delete requires confirm_token and is real
  7. data from dyad A never ends up in dyad B's adapter dir
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cama.agents import cama_dyad
from cama.agents import cama_persona
@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    yield


def _seed_exchange(dyad_id: str, raw: str, valence: float = 0.0, arousal: float = 0.0):
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(dyad_id)))
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, confidence, created_at, updated_at) "
            "VALUES (?, 'exchange', 'test_seed', 'exchange', 'durable', "
            "        'user', 1.0, ?, ?)",
            (raw, now, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO memory_affect "
            "(memory_id, valence, arousal, emotion_json, confidence, computed_at) "
            "VALUES (?, ?, ?, ?, 1.0, ?)",
            (mid, valence, arousal, json.dumps({}), now),
        )
        conn.commit()
        return mid
    finally:
        conn.close()


def _opt_in(dyad_id: str):
    cama_dyad.update_consent(
        dyad_id, {"persona_training": True}, reason="test opt-in"
    )


# ============================================================
# Consent gate
# ============================================================

def test_prepare_refuses_without_consent():
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    _seed_exchange(r["dyad_id"], "[USER] hi\n[ASSISTANT] hello")
    result = cama_persona.prepare_adapter(
        r["dyad_id"], base_model="test/dummy"
    )
    assert result["status"] == "refused"
    assert "persona_training" in result["reason"]
    # No adapter dir was created.
    assert not (cama_dyad.dyad_dir(r["dyad_id"]) / "persona").exists()


def test_prepare_succeeds_with_consent():
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] hi\n[ASSISTANT] hello there")
    result = cama_persona.prepare_adapter(
        r["dyad_id"], base_model="test/dummy"
    )
    assert result["status"] == "prepared"
    assert result["version"] == "v1"
    assert result["exchange_count"] >= 1
    assert result["identity_pin_count"] >= 1


# ============================================================
# Data export + fingerprinting
# ============================================================

def test_prepared_dir_contains_expected_files():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    res = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    adir = Path(res["adapter_dir"])
    for fname in (
        "metadata.json", "training_data.jsonl",
        "training_data.sha256", "identity_pins.jsonl",
        "identity_pins.sha256",
    ):
        assert (adir / fname).exists(), f"missing {fname}"


def test_verify_passes_for_intact_adapter():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    res = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    v = cama_persona.verify_adapter(r["dyad_id"], res["version"])
    assert v["ok"] is True


def test_verify_catches_tampering():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    res = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    adir = Path(res["adapter_dir"])
    # Tamper with the training data after fingerprinting.
    with (adir / "training_data.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"messages": [{"role": "user", "content": "INJECTED"}]}\n')
    v = cama_persona.verify_adapter(r["dyad_id"], res["version"])
    assert v["ok"] is False
    assert v["reason"] == "training_data_mismatch"


def test_identity_pins_are_non_empty():
    # Every freshly-init'd dyad has a seeded identity teaching (is_core=1).
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    res = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    pins_path = Path(res["adapter_dir"]) / "identity_pins.jsonl"
    lines = pins_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    msgs = first["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "assistant"
    assert "Y" in msgs[-1]["content"]  # the AI's name


def test_no_exchanges_returns_no_exchanges_status():
    # Dyad has no exchanges seeded.
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    res = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    assert res["status"] == "no_exchanges"


# ============================================================
# Versioning
# ============================================================

def test_versions_increment():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a1")
    v1 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a2")
    v2 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a3")
    v3 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    assert v1["version"] == "v1"
    assert v2["version"] == "v2"
    assert v3["version"] == "v3"
    assert len(cama_persona.list_adapters(r["dyad_id"])) == 3


# ============================================================
# Current / rollback
# ============================================================

def test_set_and_get_current():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    v1 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    cama_persona.set_current_adapter(r["dyad_id"], v1["version"])
    cur = cama_persona.get_current_adapter(r["dyad_id"])
    assert cur["current_version"] == "v1"


def test_rollback_returns_to_previous():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a1")
    v1 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a2")
    v2 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    cama_persona.set_current_adapter(r["dyad_id"], v2["version"])
    res = cama_persona.rollback_to_previous(r["dyad_id"])
    assert res["status"] == "rolled_back"
    assert res["now_current"] == "v1"


def test_rollback_with_no_previous():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    v1 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    cama_persona.set_current_adapter(r["dyad_id"], v1["version"])
    res = cama_persona.rollback_to_previous(r["dyad_id"])
    assert res["status"] == "no_previous"


# ============================================================
# Delete
# ============================================================

def test_delete_requires_confirm_token():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    v1 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    with pytest.raises(PermissionError):
        cama_persona.delete_adapter(
            r["dyad_id"], v1["version"], confirm_token="wrong"
        )
    assert Path(v1["adapter_dir"]).exists()


def test_delete_is_real_and_clears_current():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in(r["dyad_id"])
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    v1 = cama_persona.prepare_adapter(r["dyad_id"], base_model="test/dummy")
    cama_persona.set_current_adapter(r["dyad_id"], v1["version"])
    cama_persona.delete_adapter(
        r["dyad_id"], v1["version"], confirm_token=v1["version"]
    )
    assert not Path(v1["adapter_dir"]).exists()
    assert cama_persona.get_current_adapter(r["dyad_id"]) is None


# ============================================================
# Cross-dyad isolation
# ============================================================

def test_dyad_a_data_does_not_leak_into_dyad_b_adapter():
    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in(a["dyad_id"])
    _opt_in(b["dyad_id"])
    _seed_exchange(a["dyad_id"], "[USER] q\n[ASSISTANT] ALICE_PRIVATE_RESPONSE")
    _seed_exchange(b["dyad_id"], "[USER] q\n[ASSISTANT] BOB_PRIVATE_RESPONSE")

    ra = cama_persona.prepare_adapter(a["dyad_id"], base_model="test/dummy")
    rb = cama_persona.prepare_adapter(b["dyad_id"], base_model="test/dummy")

    a_data = (Path(ra["adapter_dir"]) / "training_data.jsonl").read_text()
    b_data = (Path(rb["adapter_dir"]) / "training_data.jsonl").read_text()

    assert "ALICE_PRIVATE_RESPONSE" in a_data
    assert "ALICE_PRIVATE_RESPONSE" not in b_data
    assert "BOB_PRIVATE_RESPONSE" in b_data
    assert "BOB_PRIVATE_RESPONSE" not in a_data


def test_no_persona_training_consent_does_not_create_persona_dir():
    # Even creating a dyad and seeding exchanges should not auto-create the
    # persona/ subtree -- it only appears on the first consented prepare.
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    assert not (cama_dyad.dyad_dir(r["dyad_id"]) / "persona").exists()
