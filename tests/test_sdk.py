"""Tests for the cama.sdk client.

These tests are end-to-end: a real FastAPI app runs in-process via
httpx's ASGI transport, the SDK client is pointed at it, and we
exercise the full HTTP path, including the bearer-token auth,
provenance enforcement, and counterweight injection. If these pass,
the SDK and the API are talking the contract published in API.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cama.sdk import (
    CAMA,
    Affect,
    CamaConfirmHeaderMissingError,
    CamaDyadScopeError,
    CamaEnumValueUnknownError,
    CamaError,
    CamaProvenanceError,
    Provenance,
)


# ---------------------------------------------------------------------------
# Test fixtures, re-use the API contract-test schema setup
# ---------------------------------------------------------------------------
def _init_memory_schema(db_path: Path) -> None:
    """Build the production schema (cama_mcp._init plus the dyad column).

    This module used to carry a hand-written subset of the memory table; it
    drifted (no trust_score / trust_reason columns, nullable updated_at) and
    passed against a schema no live CAMA has. See tests/schema_builder.py.
    """
    from schema_builder import init_production_memory_schema

    init_production_memory_schema(db_path)


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """Spin up a CAMA API in-process and return an SDK client pointed
    at it."""
    mem_db = tmp_path / "memory.db"
    keys_db = tmp_path / "api_keys.db"
    monkeypatch.setenv("CAMA_DB_PATH", str(mem_db))
    monkeypatch.setenv("CAMA_API_KEY_DB", str(keys_db))
    monkeypatch.setenv("CAMA_CONSENT_APPROVER_SECRET", "synthetic-sdk-approval")
    _init_memory_schema(mem_db)

    from cama.api.auth import create_key
    from cama.api.server import create_app

    plaintext, _fp = create_key(dyad_id="default", kind="live", name="sdk-test")
    app = create_app()
    test_client = TestClient(app)
    client = CAMA(api_key=plaintext, http_client=test_client)
    yield client, mem_db
    client.close()
    test_client.close()


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------
class TestHappyPath:
    def test_health(self, sdk):
        client, _ = sdk
        h = client.health()
        assert h["status"] in {"ok", "degraded"}
        assert h["db"] == "ok"

    def test_version(self, sdk):
        client, _ = sdk
        v = client.version()
        assert v["api"] == "v1"

    def test_create_get_delete_lifecycle(self, sdk):
        client, _ = sdk
        mem = client.memories.create(
            text="the user prefers concise summaries with citations",
            memory_type="teaching",
            provenance=Provenance.teaching(by="user"),
            consent_approval="synthetic-sdk-approval",
            consent_level="high",
        )
        assert mem.id > 0
        assert mem.status == "durable"
        assert mem.proposed_by == "user"
        assert mem.source_type == "teaching"

        fetched = client.memories.get(mem.id)
        assert fetched.id == mem.id
        assert fetched.text == mem.text

        client.memories.delete(mem.id)
        with pytest.raises(CamaDyadScopeError):
            client.memories.get(mem.id)


# ---------------------------------------------------------------------------
# Architectural-contract tests, same checks as test_api.py but via SDK
# ---------------------------------------------------------------------------
class TestProvenanceContract:
    def test_inference_by_assistant_is_forced_provisional(self, sdk):
        client, _ = sdk
        mem = client.memories.create(
            text="the user seems to like dark themes",
            memory_type="preference",
            provenance=Provenance.inference(by="assistant"),
        )
        assert mem.status == "provisional"
        assert mem.review_after is not None

    def test_teaching_by_user_stays_durable(self, sdk):
        client, _ = sdk
        mem = client.memories.create(
            text="I prefer evening sessions",
            memory_type="preference",
            provenance=Provenance.teaching(by="user"),
            consent_approval="synthetic-sdk-approval",
        )
        assert mem.status == "durable"


class TestEnumValidation:
    def test_unknown_memory_type_raises_typed_exception(self, sdk):
        client, _ = sdk
        with pytest.raises((CamaProvenanceError, CamaEnumValueUnknownError)):
            client.memories.create(
                text="x",
                memory_type="banana",  # not in canonical set
                provenance=Provenance.teaching(by="user"),
                consent_approval="synthetic-sdk-approval",
            )


class TestDestructiveGuardrails:
    def test_delete_includes_x_confirm_automatically(self, sdk):
        """The SDK is supposed to set X-Confirm so the caller doesn't
        have to think about it, but it must be set to the memory ID,
        not some default."""
        client, _ = sdk
        mem = client.memories.create(
            text="ephemeral",
            memory_type="experience",
            provenance=Provenance.exchange(by="user"),
            consent_approval="synthetic-sdk-approval",
        )
        # SDK delete should succeed
        client.memories.delete(mem.id)
        with pytest.raises(CamaDyadScopeError):
            client.memories.get(mem.id)

    def test_raw_delete_without_confirm_raises(self, sdk):
        """Sanity: if a caller bypasses the SDK convenience and uses
        the raw request method without X-Confirm, the API still refuses."""
        client, _ = sdk
        mem = client.memories.create(
            text="ephemeral",
            memory_type="experience",
            provenance=Provenance.exchange(by="user"),
            consent_approval="synthetic-sdk-approval",
        )
        with pytest.raises(CamaConfirmHeaderMissingError):
            client.request("DELETE", f"/v1/memories/{mem.id}", expect_204=True)


class TestCounterweightInjection:
    def test_negative_affect_search_triggers_injection(self, sdk):
        client, mem_db = sdk
        # Seed counterweight-tagged memories directly (the API doesn't
        # currently let us tag them via POST, that's v1.1 work). The
        # injection logic is what the SDK is exercising here.
        c = sqlite3.connect(str(mem_db))
        for cw in ["grounding", "agency", "self_compassion"]:
            c.execute(
                "INSERT INTO memories (raw_text, memory_type, source_type, "
                "proposed_by, status, counterweight_type, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"cw-{cw} anchor",
                    "experience",
                    "teaching",
                    "user",
                    "durable",
                    cw,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
        # And a regular memory that matches the search
        c.execute(
            "INSERT INTO memories (raw_text, memory_type, source_type, "
            "proposed_by, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (
                "loss is heavy",
                "experience",
                "exchange",
                "user",
                "durable",
                "2026-05-01T00:00:00Z",
                "2026-05-01T00:00:00Z",
            ),
        )
        c.commit()
        c.close()

        response = client.search(
            "loss",
            affect=Affect(
                valence=-0.7,
                arousal=0.5,
                emotions={"grief": 0.8, "sadness": 0.6},
            ),
        )
        assert response.counterweights_injected > 0
        cw_results = [r for r in response if r.is_counterweight]
        assert len(cw_results) > 0


# ---------------------------------------------------------------------------
# Threads + dyads
# ---------------------------------------------------------------------------
class TestThreadsAndDyads:
    def test_thread_start_returns_boot(self, sdk):
        client, _ = sdk
        boot = client.threads.start(user_message="morning")
        assert boot.boot_status in {"refreshed", "warm", "cold"}
        assert isinstance(boot.resonant_memories, list)

    def test_dyad_get_own_dyad_works(self, sdk):
        client, _ = sdk
        info = client.dyads.get("default")
        assert info.id == "default"
        assert info.consent.counterweights_enabled is True


# ---------------------------------------------------------------------------
# Exception model
# ---------------------------------------------------------------------------
class TestExceptionModel:
    def test_typed_exceptions_carry_contract_code(self, sdk):
        client, _ = sdk
        try:
            client.memories.create(
                text="x",
                memory_type="banana",
                provenance=Provenance.teaching(by="user"),
                consent_approval="synthetic-sdk-approval",
            )
        except CamaError as e:
            assert e.contract in {"provenance_required", "enum_value_unknown"}
            assert e.status == 422
            assert e.detail
            return
        pytest.fail("expected a typed CamaError to be raised")


def test_sdk_approval_is_not_retained_between_requests(sdk):
    client, _ = sdk
    client.memories.create(
        text="approved synthetic teaching", memory_type="teaching",
        provenance=Provenance.teaching(by="user"),
        consent_approval="synthetic-sdk-approval",
    )
    with pytest.raises(CamaError) as denied:
        client.memories.create(
            text="unapproved synthetic teaching", memory_type="teaching",
            provenance=Provenance.teaching(by="user"),
        )
    assert denied.value.body["cama"]["violated_contract"] == "consent_approver_required"
