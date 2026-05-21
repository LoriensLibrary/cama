"""Contract tests for the webhook subsystem.

Subscribe → list → delivery (via mocked transport) → list-after-delete.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _init_memory_schema(db_path: Path) -> None:
    c = sqlite3.connect(str(db_path))
    c.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text TEXT,
            memory_type TEXT,
            context TEXT,
            source_type TEXT NOT NULL,
            status TEXT DEFAULT 'durable',
            proposed_by TEXT NOT NULL,
            consent_level TEXT DEFAULT 'medium',
            review_after TEXT,
            is_core INTEGER DEFAULT 0,
            evidence TEXT,
            counterweight_type TEXT,
            dyad_id TEXT NOT NULL DEFAULT 'default',
            updated_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_affect (
            memory_id INTEGER PRIMARY KEY, valence REAL, arousal REAL,
            dominance REAL, emotion_json TEXT, confidence REAL,
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

    plaintext, _fp = create_key(dyad_id="default", kind="live", name="hook-test")
    return {"key": plaintext, "app": create_app(), "mem_db": mem_db}


@pytest.fixture
def client(env):
    with TestClient(env["app"]) as c:
        yield c


def _auth(env):
    return {"Authorization": f"Bearer {env['key']}"}


class TestWebhookCrud:
    def test_create_returns_one_shot_secret(self, client, env):
        r = client.post(
            "/v1/webhooks",
            headers=_auth(env),
            json={
                "url": "https://example.com/cama-hook",
                "events": ["memory.created"],
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["id"] > 0
        assert body["dyad_id"] == "default"
        assert body["secret"]  # plaintext shown once
        assert "shown ONCE" in body["note"]

    def test_unknown_event_returns_422(self, client, env):
        r = client.post(
            "/v1/webhooks",
            headers=_auth(env),
            json={
                "url": "https://example.com/hook",
                "events": ["memory.eaten_by_a_shark"],
            },
        )
        assert r.status_code == 422
        assert r.json()["cama"]["violated_contract"] == "enum_value_unknown"

    def test_bad_url_returns_422(self, client, env):
        r = client.post(
            "/v1/webhooks",
            headers=_auth(env),
            json={"url": "not-a-url", "events": ["memory.created"]},
        )
        assert r.status_code == 422

    def test_list_returns_subscription(self, client, env):
        client.post(
            "/v1/webhooks",
            headers=_auth(env),
            json={
                "url": "https://example.com/h",
                "events": ["memory.created"],
            },
        )
        r = client.get("/v1/webhooks", headers=_auth(env))
        assert r.status_code == 200
        items = r.json()["webhooks"]
        assert len(items) == 1
        assert items[0]["url"] == "https://example.com/h"
        # Secret never appears in list responses
        assert "secret" not in items[0]

    def test_delete_requires_confirm(self, client, env):
        created = client.post(
            "/v1/webhooks",
            headers=_auth(env),
            json={
                "url": "https://example.com/h",
                "events": ["memory.created"],
            },
        ).json()
        wid = created["id"]
        r = client.delete(f"/v1/webhooks/{wid}", headers=_auth(env))
        assert r.status_code == 400  # missing X-Confirm
        r2 = client.delete(
            f"/v1/webhooks/{wid}",
            headers={**_auth(env), "X-Confirm": str(wid)},
        )
        assert r2.status_code == 204
        # List now empty
        listed = client.get("/v1/webhooks", headers=_auth(env)).json()
        assert listed["count"] == 0


class TestWebhookDelivery:
    def test_notify_fires_subscribed_hooks(self, env, monkeypatch):
        """When notify() is called for an event a hook is subscribed
        to, the HTTP call is made and the delivery is logged."""
        from cama.api import webhooks
        from cama.api.auth import _open_keys_db

        webhooks.create_webhook(
            dyad_id="default",
            url="https://example.com/cama-hook",
            events=["memory.created"],
        )

        captured = []

        class FakeClient:
            def post(self, url, content, headers):
                captured.append({"url": url, "content": content, "headers": headers})
                return MagicMock(status_code=200)

            def close(self):
                pass

        attempts = webhooks.notify(
            "default",
            "memory.created",
            {"id": 1, "memory_type": "experience"},
            http_client=FakeClient(),
        )
        assert attempts == 1
        assert len(captured) == 1
        assert captured[0]["url"] == "https://example.com/cama-hook"
        # Body is JSON with the event payload
        body = json.loads(captured[0]["content"])
        assert body["event"] == "memory.created"
        assert body["payload"]["id"] == 1
        # X-CAMA-Event header is set
        assert captured[0]["headers"]["X-CAMA-Event"] == "memory.created"

        # Delivery is logged
        c = _open_keys_db()
        rows = c.execute("SELECT * FROM webhook_deliveries").fetchall()
        c.close()
        assert len(rows) == 1
        assert rows[0]["status_code"] == 200

    def test_notify_does_not_fire_unsubscribed_events(self, env):
        from cama.api import webhooks

        webhooks.create_webhook(
            dyad_id="default",
            url="https://example.com/h",
            events=["memory.created"],
        )
        attempts = webhooks.notify(
            "default", "memory.deleted", {"id": 1}
        )
        assert attempts == 0

    def test_notify_isolated_across_dyads(self, env):
        from cama.api import webhooks

        webhooks.create_webhook(
            dyad_id="alpha",
            url="https://alpha.example.com/h",
            events=["memory.created"],
        )
        # Notifying 'beta' must not deliver to alpha's subscription
        attempts = webhooks.notify(
            "beta", "memory.created", {"id": 1}
        )
        assert attempts == 0
