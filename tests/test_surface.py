"""Tests for cama_surface -- the user-facing audit + delete + export surface.

Properties under test:
  1. overview returns a coherent summary spanning every layer
  2. list_memories honors filters (type, status, contains)
  3. memory_detail returns affect, edges, hive references when present
  4. delete_memory requires confirm_token = str(memory_id); real cascade
  5. delete cleans up affect/embeddings/edges/FTS
  6. purge_category requires double-confirm; keep_core protects identity
  7. consent_view returns full history
  8. handoffs_view shows outgoing and incoming
  9. export_bundle produces a complete JSON snapshot
  10. export_bundle with redact=True scrubs raw_text
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cama.agents import cama_dyad, cama_quad
from cama.core import cama_surface
from cama.hive import cama_hive_protocol as hp
from cama.hive import cama_hive_resources as hr


@pytest.fixture(autouse=True)
def isolated_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    monkeypatch.setattr(hp, "HIVE_ROOT", tmp_path / "hive")
    yield


def _seed_exchange(dyad_id, raw, v=0.0, a=0.0, emo=None):
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(dyad_id)))
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO memories "
            "(raw_text, memory_type, context, source_type, status, "
            " proposed_by, confidence, created_at, updated_at) "
            "VALUES (?, 'exchange', 'seed', 'exchange', 'durable', "
            "        'user', 1.0, ?, ?)",
            (raw, now, now),
        )
        mid = cur.lastrowid
        conn.execute(
            "INSERT INTO memory_affect "
            "(memory_id, valence, arousal, emotion_json, confidence, "
            " computed_at) VALUES (?, ?, ?, ?, 1.0, ?)",
            (mid, v, a, json.dumps(emo or {}), now),
        )
        conn.commit()
        return mid
    finally:
        conn.close()


# ============================================================
# Overview
# ============================================================

def test_overview_spans_all_layers():
    r = cama_dyad.init_dyad(person_name="Maya", ai_name="Solis")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    ov = cama_surface.overview(r["dyad_id"])
    assert ov["person_name"] == "Maya"
    assert ov["ai_name"] == "Solis"
    assert ov["memory_counts"]["total_durable"] >= 1
    assert "consent" in ov
    assert "hive" in ov
    assert "domain_resources_installed" in ov
    assert "persona" in ov
    assert "handoffs" in ov


# ============================================================
# Memory listing + detail
# ============================================================

def test_list_memories_filters_by_type():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    # Default dyad already has one identity teaching + one bond inference.
    teachings = cama_surface.list_memories(
        r["dyad_id"], memory_type="teaching"
    )
    exchanges = cama_surface.list_memories(
        r["dyad_id"], memory_type="exchange"
    )
    assert any(m["memory_type"] == "teaching" for m in teachings)
    assert all(m["memory_type"] == "teaching" for m in teachings)
    assert all(m["memory_type"] == "exchange" for m in exchanges)


def test_list_memories_filters_by_contains():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _seed_exchange(r["dyad_id"], "[USER] needle\n[ASSISTANT] haystack")
    _seed_exchange(r["dyad_id"], "[USER] other\n[ASSISTANT] thing")
    hits = cama_surface.list_memories(r["dyad_id"], contains="needle")
    assert len(hits) == 1
    assert "needle" in hits[0]["preview"]


def test_memory_detail_returns_affect():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    mid = _seed_exchange(
        r["dyad_id"], "[USER] q\n[ASSISTANT] a",
        v=-0.5, a=0.7, emo={"grief": 0.8},
    )
    detail = cama_surface.memory_detail(r["dyad_id"], mid)
    assert detail["id"] == mid
    assert detail["affect"]["valence"] == -0.5
    assert detail["affect"]["emotions"]["grief"] == 0.8


def test_memory_detail_unknown_returns_error():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    detail = cama_surface.memory_detail(r["dyad_id"], 99999)
    assert detail.get("error") == "not_found"


# ============================================================
# Delete
# ============================================================

def test_delete_requires_confirm_token():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    mid = _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    with pytest.raises(PermissionError):
        cama_surface.delete_memory(r["dyad_id"], mid, confirm_token="wrong")
    # Still there.
    assert cama_surface.memory_detail(r["dyad_id"], mid)["id"] == mid


def test_delete_cascades_to_affect():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    mid = _seed_exchange(
        r["dyad_id"], "[USER] q\n[ASSISTANT] a",
        v=0.4, a=0.2,
    )
    result = cama_surface.delete_memory(
        r["dyad_id"], mid, confirm_token=str(mid)
    )
    assert result["status"] == "deleted"
    detail = cama_surface.memory_detail(r["dyad_id"], mid)
    assert detail.get("error") == "not_found"
    # And the affect row is gone.
    conn = sqlite3.connect(str(cama_dyad.dyad_db_path(r["dyad_id"])))
    try:
        affect_row = conn.execute(
            "SELECT COUNT(*) FROM memory_affect WHERE memory_id = ?",
            (mid,),
        ).fetchone()[0]
        assert affect_row == 0
    finally:
        conn.close()


def test_delete_unknown_is_not_found():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    result = cama_surface.delete_memory(
        r["dyad_id"], 99999, confirm_token="99999"
    )
    assert result["status"] == "not_found"


# ============================================================
# Purge
# ============================================================

def test_purge_requires_double_confirm():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    with pytest.raises(PermissionError):
        cama_surface.purge_category(
            r["dyad_id"], memory_type="exchange",
            confirm_double_token="wrong",
        )


def test_purge_keep_core_protects_identity_teachings():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    # Identity teaching has is_core=1; the bond inference doesn't.
    before = cama_surface.list_memories(
        r["dyad_id"], memory_type="teaching"
    )
    assert any(m["is_core"] for m in before)

    cama_surface.purge_category(
        r["dyad_id"], memory_type="teaching",
        confirm_double_token="PURGE:teaching:durable",
    )
    after = cama_surface.list_memories(
        r["dyad_id"], memory_type="teaching"
    )
    # The core identity teaching survives.
    assert any(m["is_core"] for m in after)


def test_purge_without_keep_core_removes_everything():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    cama_surface.purge_category(
        r["dyad_id"], memory_type="teaching", keep_core=False,
        confirm_double_token="PURGE:teaching:durable",
    )
    after = cama_surface.list_memories(
        r["dyad_id"], memory_type="teaching"
    )
    assert after == []


def test_purge_returns_no_matches_when_empty():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    result = cama_surface.purge_category(
        r["dyad_id"], memory_type="exchange",
        confirm_double_token="PURGE:exchange:durable",
    )
    assert result["status"] == "no_matches"


# ============================================================
# Consent
# ============================================================

def test_consent_view_includes_history():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    cama_dyad.update_consent(
        r["dyad_id"], {"hive_consume": True}, reason="test",
    )
    cv = cama_surface.consent_view(r["dyad_id"])
    assert cv["current"]["hive_consume"] is True
    assert len(cv["history"]) >= 2
    assert "defaults" in cv


# ============================================================
# Audit trails
# ============================================================

def test_handoffs_view_shows_outgoing():
    m = cama_dyad.init_dyad(person_name="Maya", ai_name="Solis")
    c = cama_dyad.init_dyad(person_name="Dani", ai_name="Halo", role="coach")
    cama_dyad.update_consent(m["dyad_id"], {"coach_handoff": True})
    cama_dyad.update_consent(c["dyad_id"], {"receive_handoffs": True})
    res = cama_quad.initiate_handoff(
        m["dyad_id"], c["dyad_id"], member_authorization=True,
    )
    view = cama_surface.handoffs_view(m["dyad_id"])
    assert len(view["outgoing"]) == 1
    assert view["outgoing"][0]["handoff_id"] == res["handoff_id"]


def test_resources_view_shows_installed():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    cama_dyad.update_consent(r["dyad_id"], {"hive_consume": True})
    # Publish a tiny resource and install it.
    src = Path(cama_dyad.VAULT_ROOT.parent) / "stub_resource"
    src.mkdir(exist_ok=True)
    (src / "config.json").write_text(json.dumps({"x": 1}))
    hr.publish_resource(
        name="stub", version="v1", resource_type="prompt_pack",
        publisher="test.publisher", content_source=src,
    )
    hr.install_resource(r["dyad_id"], "stub")
    rv = cama_surface.resources_view(r["dyad_id"])
    assert len(rv["installed"]) == 1
    assert rv["installed"][0]["name"] == "stub"


# ============================================================
# Export
# ============================================================

def test_export_bundle_is_complete():
    r = cama_dyad.init_dyad(person_name="Maya", ai_name="Solis")
    _seed_exchange(r["dyad_id"], "[USER] q\n[ASSISTANT] a")
    cama_dyad.update_consent(r["dyad_id"], {"hive_consume": True})
    bundle = cama_surface.export_bundle(r["dyad_id"])
    assert bundle["dyad_id"] == r["dyad_id"]
    assert bundle["dyad_meta"]["person_name"] == "Maya"
    assert len(bundle["memories"]) >= 3  # identity + bond + 1 exchange
    assert "hive_publish_log" in bundle
    assert "resources_installed" in bundle
    assert "persona" in bundle
    assert "handoffs" in bundle


def test_export_bundle_redact_removes_raw_text():
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _seed_exchange(
        r["dyad_id"], "[USER] PRIVATE_TEXT_SHOULD_NOT_LEAK\n[ASSISTANT] a"
    )
    bundle = cama_surface.export_bundle(r["dyad_id"], include_raw_text=False)
    flat = json.dumps(bundle)
    assert "PRIVATE_TEXT_SHOULD_NOT_LEAK" not in flat
    assert "[REDACTED]" in flat


def test_export_bundle_writes_to_file(tmp_path):
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    out = tmp_path / "export.json"
    result = cama_surface.export_bundle(r["dyad_id"], out_path=out)
    assert result["status"] == "written"
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["dyad_id"] == r["dyad_id"]
