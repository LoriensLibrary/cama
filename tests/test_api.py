"""Contract + safety primitive tests for the CAMA HTTP API v1.

The point of these tests isn't coverage for coverage's sake. They pin
down the architectural commitments published in ``API.md`` § 2 so a
future refactor cannot silently break them:

  1. Provenance NOT NULL at the API boundary
  2. Inferences cannot self-promote (forced status=provisional)
  3. Dyad scope enforced (cross-dyad reads return 404, not 403)
  4. Counterweight injection on by default for negative-affect queries
  5. Auth required on every protected endpoint
  6. Destructive endpoints require X-Confirm

If any of those tests fail, the API has lost something it explicitly
promises.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def api_dbs(tmp_path, monkeypatch):
    """Two SQLite files: one for memories, one for keys + audit log."""
    mem_db = tmp_path / "memory.db"
    keys_db = tmp_path / "api_keys.db"
    monkeypatch.setenv("CAMA_DB_PATH", str(mem_db))
    monkeypatch.setenv("CAMA_API_KEY_DB", str(keys_db))
    _init_memory_schema(mem_db)
    return {"mem": mem_db, "keys": keys_db}


@pytest.fixture
def client(api_dbs):
    """A TestClient bound to a freshly-built FastAPI app.

    Important: import the server module AFTER the env vars are set so
    module-level constants don't capture pre-fixture paths.
    """
    from cama.api.server import create_app

    return TestClient(create_app())


@pytest.fixture
def live_key(api_dbs):
    """Mint a live key bound to the 'default' dyad. Returns the
    plaintext for use in Authorization headers."""
    from cama.api.auth import create_key

    plaintext, _fp = create_key(dyad_id="default", kind="live", name="test")
    return plaintext


@pytest.fixture
def other_key(api_dbs):
    """A live key bound to a different dyad, used for cross-dyad
    isolation tests."""
    from cama.api.auth import create_key

    plaintext, _fp = create_key(dyad_id="other_dyad", kind="live", name="other")
    return plaintext


def _init_memory_schema(db_path: Path) -> None:
    """Minimal subset of the cama_mcp schema needed by the API
    endpoints under test. Keeps tests self-contained (no dependency on
    cama_mcp's init path)."""
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
        CREATE INDEX IF NOT EXISTS idx_memories_dyad ON memories(dyad_id);
        CREATE TABLE IF NOT EXISTS memory_affect (
            memory_id INTEGER PRIMARY KEY,
            valence REAL, arousal REAL, dominance REAL,
            emotion_json TEXT, confidence REAL,
            computed_at TEXT, model TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id INTEGER PRIMARY KEY,
            embedding_json TEXT, model TEXT, computed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS librarian_membership (
            librarian_id INTEGER, memory_id INTEGER, membership_strength REAL,
            assigned_by TEXT, assigned_at TEXT
        );
    """)
    c.commit()
    c.close()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth contract, every protected endpoint requires a valid bearer
# ---------------------------------------------------------------------------
class TestAuth:
    def test_missing_bearer_is_401(self, client):
        r = client.post(
            "/v1/memories",
            json={
                "text": "x",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "teaching",
            },
        )
        assert r.status_code == 401
        body = r.json()
        assert body["cama"]["violated_contract"] == "unauthorized"

    def test_malformed_bearer_is_401(self, client):
        r = client.post(
            "/v1/memories",
            headers={"Authorization": "Bearer not-a-real-key"},
            json={
                "text": "x",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "teaching",
            },
        )
        assert r.status_code == 401
        assert r.json()["cama"]["violated_contract"] == "key_invalid"

    def test_health_does_not_require_auth(self, client):
        r = client.get("/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in {"ok", "degraded"}
        assert body["db"] == "ok"

    def test_version_does_not_require_auth(self, client):
        r = client.get("/v1/version")
        assert r.status_code == 200
        assert r.json()["api"] == "v1"


# ---------------------------------------------------------------------------
# Provenance contract, POST /v1/memories must reject missing fields
# ---------------------------------------------------------------------------
class TestProvenanceContract:
    def test_missing_proposed_by_returns_422(self, client, live_key):
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "x",
                "memory_type": "experience",
                # proposed_by omitted
                "source_type": "teaching",
            },
        )
        assert r.status_code == 422

    def test_missing_source_type_returns_422(self, client, live_key):
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "x",
                "memory_type": "experience",
                "proposed_by": "user",
                # source_type omitted
            },
        )
        assert r.status_code == 422

    def test_unknown_memory_type_returns_422(self, client, live_key):
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "x",
                "memory_type": "banana",  # not in canonical enum
                "proposed_by": "user",
                "source_type": "teaching",
            },
        )
        assert r.status_code == 422

    def test_valid_teaching_creates_durable_row(self, client, live_key):
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "the user prefers concise summaries",
                "memory_type": "teaching",
                "proposed_by": "user",
                "source_type": "teaching",
                "consent_level": "high",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "durable"
        assert body["proposed_by"] == "user"
        assert body["source_type"] == "teaching"


# ---------------------------------------------------------------------------
# Inference-promotion contract, assistant+inference is forced provisional
# ---------------------------------------------------------------------------
class TestInferencePromotionContract:
    def test_assistant_inference_is_forced_provisional(self, client, live_key):
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "the user seems to like dark themes",
                "memory_type": "preference",
                "proposed_by": "assistant",
                "source_type": "inference",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "provisional", (
            "An assistant-proposed inference MUST be stored as "
            "provisional. The promote-to-durable path is gated by "
            "consent token (API.md § 4.4)."
        )
        assert body["review_after"] is not None

    def test_user_teaching_stays_durable_even_via_inference_path(
        self, client, live_key
    ):
        """User-authored writes are durable regardless of source_type."""
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "I prefer evening sessions",
                "memory_type": "preference",
                "proposed_by": "user",
                "source_type": "inference",  # unusual but legal
            },
        )
        assert r.status_code == 201
        assert r.json()["status"] == "durable"


# ---------------------------------------------------------------------------
# Dyad-scope contract
# ---------------------------------------------------------------------------
class TestDyadScope:
    def test_cross_dyad_get_returns_404_not_403(self, client, live_key, other_key):
        # Create a memory under 'default' dyad
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "default-dyad memory",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        mem_id = r.json()["id"]

        # The OWNING key can read it
        r_own = client.get(f"/v1/memories/{mem_id}", headers=_auth(live_key))
        assert r_own.status_code == 200

        # The OTHER-dyad key gets 404, SQL filter enforces isolation
        r_other = client.get(f"/v1/memories/{mem_id}", headers=_auth(other_key))
        assert r_other.status_code == 404
        assert r_other.json()["cama"]["violated_contract"] == "dyad_scope"

    def test_cross_dyad_search_does_not_leak(self, client, live_key, other_key):
        # Default dyad writes a memory with a uniquely-recognizable token
        client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "tenant_isolation_canary_token_xyzzy",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        # other-dyad key searches for the canary; must not see it
        r = client.post(
            "/v1/search",
            headers=_auth(other_key),
            json={"query": "xyzzy"},
        )
        assert r.status_code == 200
        results = r.json()["results"]
        for item in results:
            assert "xyzzy" not in item["text"], (
                f"cross-dyad leak: other-dyad key retrieved {item}"
            )

    def test_cross_dyad_dyad_get_returns_404(self, client, other_key):
        r = client.get("/v1/dyads/default", headers=_auth(other_key))
        assert r.status_code == 404
        assert r.json()["cama"]["violated_contract"] == "dyad_scope"

    def test_own_dyad_get_returns_200(self, client, live_key):
        r = client.get("/v1/dyads/default", headers=_auth(live_key))
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "default"
        assert body["consent"]["counterweights_enabled"] is True


# ---------------------------------------------------------------------------
# Counterweight injection contract, on by default for negative affect
# ---------------------------------------------------------------------------
class TestCounterweightInjection:
    def _seed_counterweights(self, mem_db: Path) -> None:
        """Insert a few counterweight memories so the search has something
        to inject."""
        c = sqlite3.connect(str(mem_db))
        for i, cw_type in enumerate(
            ["grounding", "agency", "connection", "self_compassion"]
        ):
            c.execute(
                "INSERT INTO memories (raw_text, memory_type, source_type, "
                "proposed_by, status, counterweight_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"cw-{i}: anchor memory",
                    "experience",
                    "teaching",
                    "user",
                    "durable",
                    cw_type,
                    "2026-01-01T00:00:00Z",
                ),
            )
        # Also a regular memory that will match the search query
        c.execute(
            "INSERT INTO memories (raw_text, memory_type, source_type, "
            "proposed_by, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "loss is heavy this week",
                "experience",
                "exchange",
                "user",
                "durable",
                "2026-05-01T00:00:00Z",
            ),
        )
        c.commit()
        c.close()

    def test_negative_affect_query_triggers_injection(
        self, client, live_key, api_dbs
    ):
        self._seed_counterweights(api_dbs["mem"])
        r = client.post(
            "/v1/search",
            headers=_auth(live_key),
            json={
                "query": "loss",
                "affect": {
                    "valence": -0.7,
                    "arousal": 0.5,
                    "emotions": {"grief": 0.8, "sadness": 0.6},
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["routing"]["counterweights_injected"] > 0, (
            "Counterweight injection MUST happen by default on "
            "negative-affect queries (API.md § 4.5)."
        )
        cw_results = [x for x in body["results"] if x["is_counterweight"]]
        assert len(cw_results) > 0

    def test_neutral_affect_query_does_not_inject(
        self, client, live_key, api_dbs
    ):
        self._seed_counterweights(api_dbs["mem"])
        r = client.post(
            "/v1/search",
            headers=_auth(live_key),
            json={
                "query": "loss",
                "affect": {"valence": 0.1, "arousal": 0.0, "emotions": {}},
            },
        )
        assert r.status_code == 200
        assert r.json()["routing"]["counterweights_injected"] == 0


# ---------------------------------------------------------------------------
# Destructive-endpoint guardrails
# ---------------------------------------------------------------------------
class TestDestructiveGuardrails:
    def _create_memory(self, client, live_key) -> int:
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "to be deleted",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        return r.json()["id"]

    def test_delete_without_confirm_header_returns_400(self, client, live_key):
        mem_id = self._create_memory(client, live_key)
        r = client.delete(
            f"/v1/memories/{mem_id}", headers=_auth(live_key)
        )
        assert r.status_code == 400
        assert (
            r.json()["cama"]["violated_contract"] == "confirm_header_missing"
        )

    def test_delete_with_wrong_confirm_returns_400(self, client, live_key):
        mem_id = self._create_memory(client, live_key)
        r = client.delete(
            f"/v1/memories/{mem_id}",
            headers={**_auth(live_key), "X-Confirm": "wrong"},
        )
        assert r.status_code == 400

    def test_delete_with_correct_confirm_succeeds(self, client, live_key):
        mem_id = self._create_memory(client, live_key)
        r = client.delete(
            f"/v1/memories/{mem_id}",
            headers={**_auth(live_key), "X-Confirm": str(mem_id)},
        )
        assert r.status_code == 204
        # Subsequent GET returns 404
        r2 = client.get(f"/v1/memories/{mem_id}", headers=_auth(live_key))
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Phase-1 librarian routing through /v1/search
# ---------------------------------------------------------------------------
# These tests pin down the architectural commitment in RETRIEVAL.md § 2 +
# API.md § 4: /v1/search MUST attempt librarian-routed retrieval first
# (the real CAMA Phase-1 pipeline) and fall back to keyword LIKE only when
# the dyad's routing index is empty. Before PR #17 the endpoint was
# keyword-LIKE only, these tests guard the upgrade against regression.
class TestLibrarianRouting:
    def _seed_librarian(
        self,
        mem_db: Path,
        *,
        librarian_name: str,
        keywords: list[str],
        memory_id: int,
        membership_strength: float = 0.9,
    ) -> int:
        """Insert one librarian + one membership row pointing to memory_id.
        Returns the librarian_id. Uses raw SQL, bypasses the librarian
        module's populate() so the test stays independent of which
        starter set populate() would build."""
        import json as _json

        c = sqlite3.connect(str(mem_db))
        # The librarians table is created lazily by the librarian module's
        # init_schema(); pre-create it here with the same shape so the
        # test doesn't depend on the module having been imported yet.
        c.executescript("""
            CREATE TABLE IF NOT EXISTS librarians (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                parent_id INTEGER,
                routing_keywords TEXT,
                routing_affect TEXT,
                scoring_weights TEXT,
                activation_score REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT,
                access_count INTEGER DEFAULT 0,
                member_count INTEGER DEFAULT 0,
                notes TEXT
            );
        """)
        cur = c.execute(
            "INSERT INTO librarians "
            "(name, category, description, routing_keywords, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                librarian_name,
                "topic",
                f"Test librarian for {keywords!r}",
                _json.dumps(keywords),
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        lib_id = cur.lastrowid
        c.execute(
            "INSERT INTO librarian_membership "
            "(librarian_id, memory_id, membership_strength, "
            "assigned_by, assigned_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                lib_id,
                memory_id,
                membership_strength,
                "test",
                "2026-01-01T00:00:00Z",
            ),
        )
        c.commit()
        c.close()
        return lib_id

    def test_librarian_routed_path_activates_when_index_populated(
        self, client, live_key, api_dbs
    ):
        # Write a memory through the API, then bind it to a librarian
        # whose keyword matches a query we'll issue.
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "thinking about the fellowship application timing",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        assert r.status_code == 201
        mem_id = r.json()["id"]
        self._seed_librarian(
            api_dbs["mem"],
            librarian_name="topic_fellowship",
            keywords=["fellowship"],
            memory_id=mem_id,
        )
        # Query whose word matches the librarian's routing_keywords
        r2 = client.post(
            "/v1/search",
            headers=_auth(live_key),
            json={"query": "fellowship"},
        )
        assert r2.status_code == 200
        body = r2.json()
        # The real librarian pipeline ran, not the keyword fallback
        assert body["routing"]["phase"] == "1", (
            "Librarian routing did not activate, /v1/search regressed "
            "to keyword-only fallback. See RETRIEVAL.md § 2."
        )
        assert body["routing"]["librarians_activated"] >= 1
        # The seeded memory surfaced via the librarian membership join
        ids = [item["id"] for item in body["results"]]
        assert mem_id in ids
        # Membership-strength flowed into the surfaced score
        hit = next(item for item in body["results"] if item["id"] == mem_id)
        assert hit["score"] >= 0.5
        assert hit["score_breakdown"]["relational"] >= 0.5

    def test_falls_back_to_keyword_when_no_librarian_matches(
        self, client, live_key, api_dbs
    ):
        # Write a memory but DON'T bind it to any librarian. The
        # librarian path should return 0 rows and fall back to LIKE.
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "totally orphaned memory about xylophones",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        mem_id = r.json()["id"]
        r2 = client.post(
            "/v1/search",
            headers=_auth(live_key),
            json={"query": "xylophones"},
        )
        assert r2.status_code == 200
        body = r2.json()
        # Fallback path took over (architectural commitment: the API
        # contract doesn't degrade just because routing is empty).
        assert body["routing"]["phase"] == "1_keyword_fallback"
        assert body["routing"]["librarians_activated"] == 0
        assert mem_id in [item["id"] for item in body["results"]]

    def test_librarian_path_respects_dyad_scope(
        self, client, live_key, other_key, api_dbs
    ):
        # Seed a memory + librarian binding in the 'default' dyad
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "private librarian-bound canary banana123",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        mem_id = r.json()["id"]
        self._seed_librarian(
            api_dbs["mem"],
            librarian_name="topic_banana",
            keywords=["banana123"],
            memory_id=mem_id,
        )
        # The OTHER-dyad key issues the same query. The librarian
        # routing finds the librarian (routing index is dyad-agnostic by
        # design, it's a query-time concept), but the JOIN's
        # m.dyad_id = ? filter MUST prevent the row from surfacing.
        r2 = client.post(
            "/v1/search",
            headers=_auth(other_key),
            json={"query": "banana123"},
        )
        assert r2.status_code == 200
        for item in r2.json()["results"]:
            assert "banana123" not in item["text"], (
                "Librarian-routed search leaked across dyads, "
                "the dyad_id filter in the JOIN is broken."
            )

    def test_counterweight_injection_runs_alongside_librarian_path(
        self, client, live_key, api_dbs
    ):
        # Seed counterweight rows directly + a librarian-bound memory
        # in the same dyad. Issue a negative-affect query and assert
        # both the librarian hit AND the counterweight appear.
        c = sqlite3.connect(str(api_dbs["mem"]))
        for cw_type in ("grounding", "agency"):
            c.execute(
                "INSERT INTO memories (raw_text, memory_type, source_type, "
                "proposed_by, status, counterweight_type, dyad_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"counterweight-{cw_type}",
                    "experience",
                    "teaching",
                    "user",
                    "durable",
                    cw_type,
                    "default",
                    "2026-01-01T00:00:00Z",
                ),
            )
        c.commit()
        c.close()
        r = client.post(
            "/v1/memories",
            headers=_auth(live_key),
            json={
                "text": "heavy week, loss is overwhelming",
                "memory_type": "experience",
                "proposed_by": "user",
                "source_type": "exchange",
            },
        )
        mem_id = r.json()["id"]
        self._seed_librarian(
            api_dbs["mem"],
            librarian_name="affect_grief_window",
            keywords=["loss", "grief"],
            memory_id=mem_id,
        )
        r2 = client.post(
            "/v1/search",
            headers=_auth(live_key),
            json={
                "query": "loss",
                "affect": {
                    "valence": -0.7,
                    "arousal": 0.5,
                    "emotions": {"grief": 0.9},
                },
            },
        )
        assert r2.status_code == 200
        body = r2.json()
        # Librarian path ran AND counterweights were injected on top
        assert body["routing"]["phase"] == "1"
        assert body["routing"]["librarians_activated"] >= 1
        assert body["routing"]["counterweights_injected"] > 0
        # Both result kinds are present
        regular = [x for x in body["results"] if not x["is_counterweight"]]
        cw = [x for x in body["results"] if x["is_counterweight"]]
        assert len(regular) >= 1
        assert len(cw) >= 1


# ---------------------------------------------------------------------------
# OpenAPI shape, the closed enum set is published
# ---------------------------------------------------------------------------
class TestOpenAPIShape:
    def test_openapi_publishes_provenance_enum(self, client):
        r = client.get("/v1/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        # Pydantic emits the Literal as an enum in components.schemas
        # Look it up via the request body model
        schemas = spec.get("components", {}).get("schemas", {})
        mem_create = schemas.get("MemoryCreateRequest", {})
        properties = mem_create.get("properties", {})
        assert "proposed_by" in properties
        assert "source_type" in properties
