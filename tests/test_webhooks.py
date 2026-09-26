"""Contract tests for the webhook subsystem.

Subscribe → list → delivery (via mocked transport) → list-after-delete.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from contextlib import contextmanager
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
    monkeypatch.setenv("CAMA_WEBHOOK_MASTER_SECRET", "synthetic-test-master-key-32-bytes-long")
    monkeypatch.setenv("CAMA_WEBHOOK_ALLOWED_HOSTS", "example.com,alpha.example.com")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ])
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

        _, secret = webhooks.create_webhook(
            dyad_id="default",
            url="https://example.com/cama-hook",
            events=["memory.created"],
        )

        captured = []

        class FakeClient:
            @contextmanager
            def stream(self, method, url, content, headers, **kwargs):
                captured.append({"url": url, "content": content, "headers": headers})
                assert method == "POST"
                assert kwargs["follow_redirects"] is False
                assert kwargs["extensions"]["sni_hostname"] == "example.com"
                yield MagicMock(status_code=200)

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
        assert str(captured[0]["url"]) == "https://93.184.216.34/cama-hook"
        assert captured[0]["headers"]["Host"] == "example.com"
        assert captured[0]["headers"]["X-CAMA-Signature"] == webhooks._sign(captured[0]["content"], secret)
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


@pytest.mark.parametrize('url', [
    'http://example.com/h', 'https://example.com:8443/h',
    'https://user:password@example.com/h', 'https://example.com/h#fragment',
    'https://example.com.attacker.test/h', 'https://localhost/h',
    'https://127.0.0.1/h', 'https://[::1]/h', 'https://example.com/\nheader',
])
def test_unsafe_registration_rejected(client, env, url):
    response = client.post('/v1/webhooks', headers=_auth(env), json={
        'url': url, 'events': ['memory.created'],
    })
    assert response.status_code == 422


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254',
                                     '::1', 'fc00::1', '::ffff:127.0.0.1', '224.0.0.1'])
def test_private_or_mixed_dns_rejected(client, env, monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)),
    ])
    assert client.post('/v1/webhooks', headers=_auth(env), json={
        'url': 'https://example.com/h', 'events': ['memory.created'],
    }).status_code == 422


@pytest.mark.parametrize('setting', ['CAMA_WEBHOOK_MASTER_SECRET', 'CAMA_WEBHOOK_ALLOWED_HOSTS'])
def test_unconfigured_webhooks_fail_closed(client, env, monkeypatch, setting):
    monkeypatch.delenv(setting)
    assert client.post('/v1/webhooks', headers=_auth(env), json={
        'url': 'https://example.com/h', 'events': ['memory.created'],
    }).status_code == 503


@pytest.mark.parametrize('change', ['dns', 'master', 'legacy', 'allowlist'])
def test_delivery_revalidates_authority_and_destination(env, monkeypatch, change):
    from cama.api import webhooks
    from cama.api.auth import _open_keys_db

    wid, _ = webhooks.create_webhook(dyad_id='default', url='https://example.com/h', events=['memory.created'])
    if change == 'dns':
        monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443)),
        ])
    elif change == 'master':
        monkeypatch.setenv('CAMA_WEBHOOK_MASTER_SECRET', 'different-test-master-key-32-bytes-long')
    elif change == 'allowlist':
        monkeypatch.setenv('CAMA_WEBHOOK_ALLOWED_HOSTS', 'other.example.com')
    else:
        with _open_keys_db() as conn:
            conn.execute('UPDATE webhooks SET signing_nonce=NULL WHERE id=?', (wid,))
    client = MagicMock()
    assert webhooks.notify('default', 'memory.created', {'id': 1}, http_client=client) == 1
    client.stream.assert_not_called()
    conn = _open_keys_db()
    try:
        row = conn.execute('SELECT status_code, error FROM webhook_deliveries').fetchone()
        assert row['status_code'] is None
        assert row['error']
    finally:
        conn.close()


def test_signature_is_secret_specific_and_database_has_no_plaintext(env):
    from cama.api import webhooks
    from cama.api.auth import _open_keys_db

    _, first = webhooks.create_webhook(dyad_id='default', url='https://example.com/h', events=['memory.created'])
    _, second = webhooks.create_webhook(dyad_id='default', url='https://example.com/h', events=['memory.created'])
    assert first != second
    assert webhooks._sign(b'original', first) != webhooks._sign(b'tampered', first)
    assert webhooks._sign(b'original', first) != webhooks._sign(b'original', second)
    conn = _open_keys_db()
    try:
        dump = '\n'.join(conn.iterdump())
        assert first not in dump and second not in dump
    finally:
        conn.close()


def test_pinned_connection_preserves_tls_hostname_and_does_not_follow_redirect(env):
    import httpx

    from cama.api import webhooks

    webhooks.create_webhook(dyad_id='default', url='https://example.com/h', events=['memory.created'])
    calls = []

    class Stream:
        def start_tls(self, ssl_context, server_hostname, timeout):
            assert ssl_context.check_hostname
            calls.append(('tls', server_hostname))
            return self

        def write(self, buffer, timeout=None):
            pass

        def read(self, max_bytes, timeout=None):
            return b'HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/secret\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'

        def close(self):
            pass

        def get_extra_info(self, info):
            return None

    class Backend:
        def connect_tcp(self, host, port, **kwargs):
            calls.append(('tcp', host))
            return Stream()

    transport = httpx.HTTPTransport()
    transport._pool._network_backend = Backend()
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=True) as client:
        webhooks.notify('default', 'memory.created', {'id': 1}, http_client=client)
    assert calls == [('tcp', '93.184.216.34'), ('tls', 'example.com')]


def test_notify_isolated_across_dyads(env):
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


def test_legacy_schema_migration_preserves_subscription(env):
    from cama.api import webhooks
    from cama.api.auth import _open_keys_db

    with _open_keys_db() as conn:
        conn.execute("DROP TABLE IF EXISTS webhooks")
        conn.execute("CREATE TABLE webhooks (id INTEGER PRIMARY KEY, dyad_id TEXT, url TEXT, events_json TEXT, secret_hash TEXT, created_at TEXT, revoked_at TEXT)")
        conn.execute("INSERT INTO webhooks VALUES (1, 'default', 'https://example.com/h', '[]', 'old-hash', '2026-01-01', NULL)")
    webhooks.init_webhooks_schema()
    webhooks.init_webhooks_schema()
    with _open_keys_db() as conn:
        row = conn.execute("SELECT secret_hash, signing_nonce FROM webhooks WHERE id=1").fetchone()
        assert tuple(row) == ('old-hash', None)
