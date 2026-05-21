"""CAMA Python SDK — the typed client that wraps the v1 HTTP API.

Quick start::

    from cama.sdk import CAMA, Provenance, Affect

    client = CAMA(api_key="cama_sk_live_...", endpoint="http://localhost:8080")

    # Store a teaching from the user
    mem = client.memories.create(
        text="the user prefers concise summaries with citations",
        memory_type="teaching",
        provenance=Provenance.teaching(by="user"),
        affect=Affect(valence=0.2, emotions={"trust": 0.6}),
    )

    # Search with blended retrieval + counterweight injection on by default
    results = client.search("what does the user prefer", limit=10)
    for r in results:
        print(f"[{r.score:.2f}] {r.memory_type}: {r.text[:80]}")

    # Warm-boot a new thread
    boot = client.threads.start(user_message="hey")
    print(boot.journal_excerpt)

See ``TUTORIAL.md`` for a full runnable example. The SDK speaks the
``/v1/`` API defined in ``API.md`` and surfaces structured exceptions
mapped from the RFC 7807 ``cama.violated_contract`` codes the server
returns.
"""

from cama.sdk.client import CAMA
from cama.sdk.errors import (
    CamaConfirmHeaderMissingError,
    CamaConsentTokenError,
    CamaConsentTokenExpired,
    CamaConsentTokenMismatch,
    CamaConsentTokenRequired,
    CamaDegradedModeError,
    CamaDyadLockedError,
    CamaDyadScopeError,
    CamaEnumValueUnknownError,
    CamaError,
    CamaKeyError,
    CamaOriginNotAllowedError,
    CamaPayloadTooLargeError,
    CamaProvenanceError,
    CamaRateLimitError,
)
from cama.sdk.types import (
    Affect,
    DyadInfo,
    Memory,
    Provenance,
    SearchResult,
    ThreadStart,
)

__all__ = [
    "CAMA",
    "Affect",
    "DyadInfo",
    "Memory",
    "Provenance",
    "SearchResult",
    "ThreadStart",
    "CamaError",
    "CamaProvenanceError",
    "CamaDyadScopeError",
    "CamaEnumValueUnknownError",
    "CamaConsentTokenError",
    "CamaConsentTokenRequired",
    "CamaConsentTokenExpired",
    "CamaConsentTokenMismatch",
    "CamaConfirmHeaderMissingError",
    "CamaPayloadTooLargeError",
    "CamaOriginNotAllowedError",
    "CamaKeyError",
    "CamaRateLimitError",
    "CamaDegradedModeError",
    "CamaDyadLockedError",
]
