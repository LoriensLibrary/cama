"""Tests for the compliance endpoints: dyad export, dyad delete,
consent challenge + grant, inference promotion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from schema_builder import init_production_memory_schema


def _init_memory_schema(db_path: Path) -> None:
    """Build the production schema. See tests/_schema.py for why."""
    init_production_memory_schema(db_path)


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


APPROVER = "operator-held-approver-secret"


@pytest.fixture
def approver(monkeypatch):
    """Stand in for the consent UI's credential.

    In a deployment this secret lives with the page the person actually
    clicks, not with the application server, which is the whole point:
    the API key can ask for consent but cannot answer for the human.
    """
    monkeypatch.setenv("CAMA_CONSENT_APPROVER_SECRET", APPROVER)
    return APPROVER


def _challenge(client, env, action, memory_id):
    r = client.post(
        "/v1/consent/challenge",
        headers=_auth(env),
        json={"action": action, "memory_id": memory_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _grant(client, env, action, memory_id, *, challenge_id, approval=APPROVER):
    headers = _auth(env)
    if approval is not None:
        headers["X-Consent-Approval"] = approval
    return client.post(
        "/v1/consent/grant",
        headers=headers,
        json={
            "action": action,
            "memory_id": memory_id,
            "challenge_id": challenge_id,
        },
    )


def _consent_token(client, env, memory_id):
    """Walk the full flow and return a usable token."""
    ch = _challenge(client, env, "promote_to_durable", memory_id)
    r = _grant(
        client, env, "promote_to_durable", memory_id,
        challenge_id=ch["challenge_id"],
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _inference(client, env, text="an assistant hypothesis"):
    return client.post(
        "/v1/memories",
        headers=_auth(env),
        json={
            "text": text,
            "memory_type": "preference",
            "proposed_by": "assistant",
            "source_type": "inference",
        },
    ).json()


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
    def test_full_promote_flow(self, client, env, approver):
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
        token = _consent_token(client, env, mid)

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

    def test_token_bound_to_memory_id(self, client, env, approver):
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
        token = _consent_token(client, env, m1["id"])
        # Using m1's token on m2 must fail
        r = client.patch(
            f"/v1/memories/{m2['id']}/confirm",
            headers={**_auth(env), "X-Consent-Token": token},
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


# ---------------------------------------------------------------------------
# Consent authority
# ---------------------------------------------------------------------------
class TestConsentAuthority:
    """An API key may request consent. It may not answer for the human.

    Before 2026-09-07 both endpoints took the same bearer and the grant
    accepted any payload, so a caller could create an assistant
    inference, grant itself consent without ever requesting a challenge,
    and promote its own hypothesis to durable. Each test here is one step
    of that sequence, now blocked.
    """

    def test_the_self_grant_path_is_closed_end_to_end(self, client, env, approver):
        """The original attack, in full: create an inference, skip the
        challenge, grant with only the application server's own key."""
        mem = _inference(client, env, "I think she wants this remembered")
        assert mem["status"] == "provisional"

        r = client.post(
            "/v1/consent/grant",
            headers=_auth(env),
            json={"action": "promote_to_durable", "memory_id": mem["id"]},
        )
        assert r.status_code == 401
        assert (
            r.json()["cama"]["violated_contract"] == "consent_approver_required"
        )

        # And the memory is still a hypothesis.
        got = client.get(f"/v1/memories/{mem['id']}", headers=_auth(env)).json()
        assert got["status"] == "provisional"

    def test_grant_without_approver_header_is_401(self, client, env, approver):
        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        r = _grant(
            client, env, "promote_to_durable", mem["id"],
            challenge_id=ch["challenge_id"], approval=None,
        )
        assert r.status_code == 401
        assert (
            r.json()["cama"]["violated_contract"] == "consent_approver_required"
        )

    def test_grant_with_wrong_approver_is_403(self, client, env, approver):
        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        r = _grant(
            client, env, "promote_to_durable", mem["id"],
            challenge_id=ch["challenge_id"], approval="guessed-wrong",
        )
        assert r.status_code == 403

    def test_deployment_without_approver_secret_cannot_grant(
        self, client, env, monkeypatch
    ):
        """Fail closed. If nothing in the deployment can represent a
        person saying yes, nothing may record that they did."""
        monkeypatch.delenv("CAMA_CONSENT_APPROVER_SECRET", raising=False)
        mem = _inference(client, env)
        r = client.post(
            "/v1/consent/grant",
            headers={**_auth(env), "X-Consent-Approval": "anything"},
            json={"action": "promote_to_durable", "memory_id": mem["id"]},
        )
        assert r.status_code == 503
        assert (
            r.json()["cama"]["violated_contract"]
            == "consent_approver_not_configured"
        )

    def test_grant_requires_a_challenge_id(self, client, env, approver):
        mem = _inference(client, env)
        r = _grant(
            client, env, "promote_to_durable", mem["id"], challenge_id=None,
        )
        assert r.status_code == 422
        assert (
            r.json()["cama"]["violated_contract"] == "consent_challenge_required"
        )

    def test_invented_challenge_id_is_rejected(self, client, env, approver):
        mem = _inference(client, env)
        r = _grant(
            client, env, "promote_to_durable", mem["id"],
            challenge_id="not-a-real-challenge",
        )
        assert r.status_code == 403
        assert (
            r.json()["cama"]["violated_contract"] == "consent_challenge_invalid"
        )

    def test_challenge_is_bound_to_its_memory(self, client, env, approver):
        """A challenge shown for one memory cannot be redeemed for another,
        or the person accepted a different thing than what was recorded."""
        shown = _inference(client, env, "the one she was asked about")
        other = _inference(client, env, "the one she was not")
        ch = _challenge(client, env, "promote_to_durable", shown["id"])
        r = _grant(
            client, env, "promote_to_durable", other["id"],
            challenge_id=ch["challenge_id"],
        )
        assert r.status_code == 403
        assert (
            r.json()["cama"]["violated_contract"] == "consent_challenge_invalid"
        )

    def test_challenge_is_bound_to_its_action(self, client, env, approver):
        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        r = _grant(
            client, env, "delete_memory", mem["id"],
            challenge_id=ch["challenge_id"],
        )
        assert r.status_code == 403

    def test_challenge_is_single_use(self, client, env, approver):
        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        first = _grant(
            client, env, "promote_to_durable", mem["id"],
            challenge_id=ch["challenge_id"],
        )
        assert first.status_code == 200
        second = _grant(
            client, env, "promote_to_durable", mem["id"],
            challenge_id=ch["challenge_id"],
        )
        assert second.status_code == 403
        assert (
            second.json()["cama"]["violated_contract"]
            == "consent_challenge_invalid"
        )

    def test_expired_challenge_is_rejected(self, client, env, approver):
        import sqlite3 as _sq

        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        c = _sq.connect(os.environ["CAMA_API_KEY_DB"])
        c.execute(
            "UPDATE consent_challenges SET expires_at = ? WHERE challenge_id = ?",
            ("2000-01-01T00:00:00+00:00", ch["challenge_id"]),
        )
        c.commit()
        c.close()
        r = _grant(
            client, env, "promote_to_durable", mem["id"],
            challenge_id=ch["challenge_id"],
        )
        assert r.status_code == 403

    def test_challenge_expiry_is_a_real_timestamp(self, client, env):
        """The old code built expires_at by string surgery and produced
        values like 2026-09-06T19:08:59T00:00:00+00:00."""
        from datetime import datetime, timedelta, timezone

        from cama.api.consent import CONSENT_CHALLENGE_TTL_SECONDS

        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        assert ch["expires_at"].count("T") == 1, ch["expires_at"]
        parsed = datetime.fromisoformat(ch["expires_at"])
        now = datetime.now(timezone.utc)
        assert now < parsed <= now + timedelta(
            seconds=CONSENT_CHALLENGE_TTL_SECONDS + 5
        ), f"expiry {ch['expires_at']} is not within the challenge TTL"

    def test_challenge_is_scoped_to_the_issuing_dyad(self, client, env, approver, tmp_path):
        """A challenge issued to one dyad must not be redeemable by another
        key, even one holding the approver credential."""
        from cama.api.auth import create_key

        mem = _inference(client, env)
        ch = _challenge(client, env, "promote_to_durable", mem["id"])
        other_plaintext, _ = create_key(dyad_id="someone_else", kind="live")
        r = client.post(
            "/v1/consent/grant",
            headers={
                "Authorization": f"Bearer {other_plaintext}",
                "X-Consent-Approval": APPROVER,
            },
            json={
                "action": "promote_to_durable",
                "memory_id": mem["id"],
                "challenge_id": ch["challenge_id"],
            },
        )
        assert r.status_code == 403
        assert (
            r.json()["cama"]["violated_contract"] == "consent_challenge_invalid"
        )
