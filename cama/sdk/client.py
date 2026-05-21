"""HTTP client for the CAMA v1 API.

Design notes:

* Uses ``httpx`` for the HTTP layer (already in the dev deps and a
  standard choice for modern Python SDKs).
* Resource namespaces (``client.memories``, ``client.search``,
  ``client.threads``, ``client.dyads``) keep the public surface readable.
* Every non-2xx response is converted to a typed exception via
  ``cama.sdk.errors.error_for_response`` — callers can branch on
  ``except CamaProvenanceError`` rather than inspecting status codes.
* The client accepts either a real HTTP endpoint (production) or an
  ``httpx.Client`` that's been pointed at a FastAPI TestClient (for
  in-process testing). This is the integration-test path.

The public API is intentionally small. If a caller needs raw access,
``client._http`` is the underlying ``httpx.Client`` and any unknown
endpoint can be hit via ``client.request(method, path, ...)``.
"""

from __future__ import annotations

from typing import Any

import httpx

from cama.sdk.errors import CamaError, error_for_response
from cama.sdk.types import (
    Affect,
    DyadConsent,
    DyadInfo,
    Memory,
    Provenance,
    ScoreBreakdown,
    SearchResponse,
    SearchResult,
    ThreadStart,
)


# ---------------------------------------------------------------------------
# Resource namespaces
# ---------------------------------------------------------------------------
class MemoriesResource:
    def __init__(self, parent: CAMA) -> None:
        self._parent = parent

    def create(
        self,
        text: str,
        *,
        memory_type: str,
        provenance: Provenance,
        affect: Affect | None = None,
        context: str | None = None,
        consent_level: str = "medium",
        evidence: str | None = None,
        is_core: bool = False,
    ) -> Memory:
        """Store a memory. Returns the stored record.

        For ``provenance=Provenance.inference(by="assistant")`` the
        returned ``Memory.status`` will be ``"provisional"`` and
        ``Memory.review_after`` will be set — per the API contract
        that "AI cannot self-promote teachings."
        """
        body: dict[str, Any] = {
            "text": text,
            "memory_type": memory_type,
            "proposed_by": provenance.proposed_by,
            "source_type": provenance.source_type,
            "consent_level": consent_level,
            "is_core": is_core,
        }
        if context is not None:
            body["context"] = context
        if evidence is not None:
            body["evidence"] = evidence
        if affect is not None:
            body["affect"] = affect.to_payload()
        data = self._parent.request("POST", "/v1/memories", json=body)
        return Memory.from_response(data)

    def get(self, memory_id: int) -> Memory:
        data = self._parent.request("GET", f"/v1/memories/{memory_id}")
        return Memory.from_response(data)

    def delete(self, memory_id: int) -> None:
        """Permanently delete a memory. Requires the X-Confirm header,
        which the SDK sets automatically to the memory ID."""
        self._parent.request(
            "DELETE",
            f"/v1/memories/{memory_id}",
            headers={"X-Confirm": str(memory_id)},
            expect_204=True,
        )


class ThreadsResource:
    def __init__(self, parent: CAMA) -> None:
        self._parent = parent

    def start(
        self,
        *,
        user_message: str = "",
        user_affect: Affect | None = None,
    ) -> ThreadStart:
        body: dict[str, Any] = {"user_message": user_message}
        if user_affect is not None:
            body["user_affect"] = user_affect.to_payload()
        data = self._parent.request("POST", "/v1/thread/start", json=body)
        return ThreadStart(
            boot_status=data["boot_status"],
            boot_age_min=data["boot_age_min"],
            journal_excerpt=data["journal_excerpt"],
            resonant_memories=data["resonant_memories"],
            corrections=data["corrections"],
            compliance=data["compliance"],
            performance_ms=data["performance_ms"],
        )


class DyadsResource:
    def __init__(self, parent: CAMA) -> None:
        self._parent = parent

    def get(self, dyad_id: str) -> DyadInfo:
        data = self._parent.request("GET", f"/v1/dyads/{dyad_id}")
        consent = DyadConsent(
            counterweights_enabled=data["consent"].get(
                "counterweights_enabled", True
            ),
            hive_consume=data["consent"].get("hive_consume", False),
            hive_publish=data["consent"].get("hive_publish", False),
            persona_training=data["consent"].get("persona_training", False),
        )
        return DyadInfo(
            id=data["id"],
            created_at=data["created_at"],
            last_activity_at=data.get("last_activity_at"),
            total_memories=data["total_memories"],
            consent=consent,
        )


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------
class CAMA:
    """Typed Python client for the CAMA HTTP API v1.

    Args:
        api_key: bearer token (``cama_sk_live_...`` or ``cama_sk_dev_...``)
        endpoint: base URL of the CAMA API server. Defaults to
                  ``http://127.0.0.1:8080`` for local development.
                  Ignored if ``http_client`` is supplied.
        timeout: per-request timeout in seconds. Default 30.
        http_client: optional pre-built ``httpx.Client``. When supplied,
                     the SDK uses it as-is (only adding the
                     Authorization header). The integration-test path
                     uses ``fastapi.testclient.TestClient(app)`` here
                     so requests hit the FastAPI app in-process.
                     If you pass this, ``endpoint`` and ``timeout`` are
                     ignored — they belong on the client you built.
        user_agent: optional User-Agent override.

    Example::

        from cama.sdk import CAMA, Provenance

        client = CAMA(
            api_key="cama_sk_live_...",
            endpoint="https://cama.example.com",
        )
        mem = client.memories.create(
            text="the user prefers concise summaries",
            memory_type="teaching",
            provenance=Provenance.teaching(by="user"),
        )
        results = client.search("user preferences", limit=5)
    """

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = "http://127.0.0.1:8080",
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
        user_agent: str = "cama-sdk-python/1.0",
    ) -> None:
        if not api_key.startswith("cama_sk_"):
            raise ValueError(
                "api_key must look like 'cama_sk_live_...' or "
                "'cama_sk_dev_...' (got prefix-stripped). Did you "
                "paste the raw token from cama-ops keys create?"
            )
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
        if http_client is not None:
            http_client.headers.update(headers)
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.Client(
                base_url=endpoint,
                headers=headers,
                timeout=timeout,
            )
            self._owns_http = True

        # Resource namespaces
        self.memories = MemoriesResource(self)
        self.threads = ThreadsResource(self)
        self.dyads = DyadsResource(self)

    # ---------- public convenience methods -----------------------------
    def health(self) -> dict[str, Any]:
        """``GET /v1/health``. Doesn't require auth on the server but
        the SDK sends it anyway for consistency."""
        return self.request("GET", "/v1/health")

    def version(self) -> dict[str, str]:
        return self.request("GET", "/v1/version")

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        include_provisional: bool = False,
        affect: Affect | None = None,
    ) -> SearchResponse:
        """``POST /v1/search``. Returns a SearchResponse iterable.

        Per the API contract, counterweight injection runs automatically
        for queries with strongly-negative affect — the response will
        include ``counterweights_injected > 0`` and individual results
        are flagged with ``is_counterweight``.
        """
        body: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "include_provisional": include_provisional,
        }
        if affect is not None:
            body["affect"] = affect.to_payload()
        data = self.request("POST", "/v1/search", json=body)
        results = []
        for r in data.get("results", []):
            sb = r.get("score_breakdown")
            breakdown = None
            if sb is not None:
                breakdown = ScoreBreakdown(
                    semantic=sb["semantic"],
                    affect=sb["affect"],
                    relational=sb["relational"],
                    recency=sb["recency"],
                )
            results.append(
                SearchResult(
                    id=r["id"],
                    text=r["text"],
                    memory_type=r["memory_type"],
                    proposed_by=r["proposed_by"],
                    source_type=r["source_type"],
                    score=r["score"],
                    score_breakdown=breakdown,
                    is_counterweight=r.get("is_counterweight", False),
                    created_at=r["created_at"],
                )
            )
        routing = data.get("routing", {})
        return SearchResponse(
            results=results,
            routing_phase=routing.get("phase", "?"),
            librarians_activated=routing.get("librarians_activated", 0),
            counterweights_injected=routing.get("counterweights_injected", 0),
            latency_ms=routing.get("latency_ms", 0.0),
            warnings=data.get("warnings", []),
        )

    # ---------- low-level request ---------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_204: bool = False,
    ) -> dict[str, Any]:
        """Send a request and return the parsed JSON body.

        Non-2xx responses are converted to typed exceptions via
        ``cama.sdk.errors.error_for_response``. The exception carries
        the full RFC 7807 body in ``.body`` for advanced inspection.
        """
        response = self._http.request(
            method=method,
            url=path,
            json=json,
            headers=headers,
        )
        if 200 <= response.status_code < 300:
            if expect_204 or response.status_code == 204:
                return {}
            try:
                return response.json()
            except ValueError:
                return {}
        try:
            body = response.json()
        except ValueError:
            body = None
        raise error_for_response(response.status_code, body)

    # ---------- context manager + cleanup -------------------------------
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> CAMA:
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()


# Re-export the contract-violation exception base for ``except CamaError``
__all__ = ["CAMA", "CamaError"]
