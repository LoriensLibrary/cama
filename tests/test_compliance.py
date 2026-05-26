"""Tests for the compliance endpoints: dyad export, dyad delete,
consent challenge + grant, inference promotion."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _init_memory_schema(db_path: Path) -> None:
    c = sqlite3.connect(str(db_path))
    c.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text TEXT, memory_type TEXT, context TEXT,
            source_type TEXT NOT NULL, status TEXT DEFAULT 'durable',
            proposed_by TEXT NOT NULL, consent_level TEXT DEFAULT 'medium',
            review_after TEXT, is_core INTEGER DEFAULT 0, evidence TEXT,
            counterweight_type TEXT,
            dyad_id TEXT NOT NULL DEFAULT 'default',
            updated_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_affect (
            memory_id INTEGER PRIMARY KEY,
            valence REAL, arousal REAL, dominance REAL,
            emotion_json TEXT, confidence REAL,
            computed_at TEXT, model TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id INTEGER PRIMARY KEY, embedding_json TEXT,
            model TEXT, computed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS librarian_membership (
            librarian_id INTEGER, memory_id INTEGER,
            membership_strength REAL, assigned_by TEXT, assigned_at TEXT
        );
    """)
    c.commit()
    c.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    mem_db = tmp_path / "memory.db"
    keys_db = tmp_path / "api_keys.db"
    monkeypatch.setenv("CAMA_DB_PATH", str(mem_db))
    monkeypatch.setenv("CAMA_API_KEY_DB", str(keys_db))
    _init_memory_schema(mem_db)

    from cama.api.auth import create_key
    from cama.api.server import create_app

    plaintext, _ = create_key(dyad_id="default", kind="live")
    return {"key": plaintext, "app": create_app()}


@pytest.fixture
def client(env):
    with TestClient(env["app"]) as c:
        yield c


def _auth(env):
    return {"Authorization": f"Bearer {env['key']}"}


# ---------------------------------------------------------------------------
# GDPR export
# ---------------------------------------------------------------------------
class TestDyadExport:
    def test_export_returns_all_memories(self, client, env):
        for i in range(3):
            client.post(
                "/v1/memories",
                headers=_auth(env),
                json={
                    "text": f"memory {i}",
                    "memory_type": "experience",
                    "proposed_by": "user",
                    "source_type": "exchange",
                },
            )
        r = client.get("/v1/dyads/default/export", headers=_auth(env))
        assert r.status_code == 200
        body = r.json()
        assert body["format"] == "cama-export-v1"
        assert len(body["memories"]) == 3
        assert all(m["dyad_id"] == "default" for m in body["memories"])

    def test_export_cross_dyad_returns_404(self, client, env):
        r = client.get(
            "/v1/dyads/some_other_dyad/export", headers=_auth(env)
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Right-to-erasure: full dyad delete
# ---------------------------------------------------------------------------
class TestDyadDelete:
    def test_requires_double_confirm(self, client, env):
        # Missing both
        r = client.request("DELETE", "/v1/dyads/default", headers=_auth(env))
        assert r.status_code == 400
        # Only first confirm
        r = client.request(
            "DELETE",
            "/v1/dyads/default",
            headers={**_auth(env), "X-Confirm": "default"},
        )
        assert r.status_code == 400

    def test_delete_wipes_all_memories_and_returns_merkle(self, client, env):
        for i in range(5):
            client.post(
                "/v1/memories",
                headers=_auth(env),
                json={
                    "text": f"m{i}",
                    "memory_type": "experience",
                    "proposed_by": "user",
                    "source_type": "exchange",
                },
            )
        r = client.request(
            "DELETE",
            "/v1/dyads/default",
            headers={
                **_auth(env),
                "X-Confirm": "default",
                "X-Confirm-Again": "I-understand-this-is-permanent",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["counts"]["memories"] == 5
        # Merkle root present but IDs themselves not exposed
        assert len(body["deleted_ids_merkle_root"]) == 64
        assert "deleted_ids" not in body

        # Subsequent export returns zero memories
        exp = client.get("/v1/dyads/default/export", headers=_auth(env))
        assert exp.json()["memories"] == []


# ---------------------------------------------------------------------------
# Consent token flow
# ---------------------------------------------------------------------------
class TestConsentFlow:
    def test_full_promote_flow(self, client, env):
        # 1. Create an assistant-inference (will be provisional)
        created = client.post(
            "/v1/memories",
            headers=_auth(env),
            json={
                "text": "the user seems to prefer dark themes",
                "memory_type": "preference",
                "proposed_by": "assistant",
                "source_type": "inference",
            },
        ).json()
        assert created["status"] == "provisional"
        mid = created["id"]

        # 2. Try to confirm WITHOUT a token, should fail
        r = client.patch(
            f"/v1/memories/{mid}/confirm",
            headers=_auth(env),
        )
        assert r.status_code == 401
        assert r.json()["cama"]["violated_contract"] == "consent_token_required"

        # 3. Grant a consent token via the flow
        client.post(
            "/v1/consent/challenge",
            headers=_auth(env),
            json={"action": "promote_to_durable", "memory_id": mid},
        )
        grant = client.post(
            "/v1/consent/grant",
            headers=_auth(env),
            json={"action": "promote_to_durable", "memory_id": mid},
        ).json()
        token = grant["token"]

        # 4. Confirm WITH the token, succeeds; status flips to durable
        r = client.patch(
            f"/v1/memories/{mid}/confirm",
            headers={**_auth(env), "X-Consent-Token": token},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "durable"

        # 5. Token is one-shot, re-using it fails
        r2 = client.patch(
            f"/v1/memories/{mid}/confirm",
            headers={**_auth(env), "X-Consent-Token": token},
        )
        assert r2.status_code == 403
        assert (
            r2.json()["cama"]["violated_contract"] == "consent_token_mismatch"
        )

    def test_token_bound_to_memory_id(self, client, env):
        m1 = client.post(
            "/v1/memories",
            headers=_auth(env),
            json={
                "text": "first",
                "memory_type": "preference",
                "proposed_by": "assistant",
                "source_type": "inference",
            },
        ).json()
        m2 = client.post(
            "/v1/memories",
            headers=_auth(env),
            json={
                "text": "second",
                "memory_type": "preference",
                "proposed_by": "assistant",
                "source_type": "inference",
            },
        ).json()
        grant = client.post(
            "/v1/consent/grant",
            headers=_auth(env),
            json={"action": "promote_to_durable", "memory_id": m1["id"]},
        ).json()
        # Using m1's token on m2 must fail
        r = client.patch(
            f"/v1/memories/{m2['id']}/confirm",
            headers={**_auth(env), "X-Consent-Token": grant["token"]},
        )
        assert r.status_code == 403

    def test_garbled_token_returns_403(self, client, env):
        mem = client.post(
            "/v1/memories",
            headers=_auth(env),
            json={
                "text": "x",
                "memory_type": "preference",
                "proposed_by": "assistant",
                "source_type": "inference",
            },
        ).json()
        r = client.patch(
            f"/v1/memories/{mem['id']}/confirm",
            headers={**_auth(env), "X-Consent-Token": "garbage.notreal"},
        )
        assert r.status_code == 403
