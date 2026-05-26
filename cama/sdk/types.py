"""Typed data classes for the CAMA SDK.

These are dataclasses with explicit fields rather than re-using
``cama.api.schemas`` Pydantic models. The split is on purpose: the
schemas module describes the *server* contract, and we don't want the
client to import server-only dependencies (FastAPI). The SDK types
mirror the API schemas with the minimum surface a consumer needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProposedBy = Literal["user", "assistant", "system"]
SourceType = Literal["teaching", "inference", "exchange", "journal"]
MemoryStatus = Literal["durable", "provisional", "expired", "rejected"]


# ---------------------------------------------------------------------------
# Provenance helpers, the architectural contract surfaced as ergonomic API
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Provenance:
    """The provenance pair the API requires on every memory write.

    The named constructors (``teaching``, ``inference``, ``exchange``,
    ``journal``) make the most common call sites read like English::

        provenance=Provenance.teaching(by="user")
        provenance=Provenance.inference(by="assistant")

    A direct instantiation works too::

        provenance=Provenance(proposed_by="user", source_type="teaching")
    """

    proposed_by: ProposedBy
    source_type: SourceType

    @classmethod
    def teaching(cls, *, by: ProposedBy = "user") -> Provenance:
        return cls(proposed_by=by, source_type="teaching")

    @classmethod
    def inference(cls, *, by: ProposedBy = "assistant") -> Provenance:
        """Assistant-proposed inferences land as ``provisional`` per the
        v1 API contract, the consent token flow is required to promote
        them to ``durable``."""
        return cls(proposed_by=by, source_type="inference")

    @classmethod
    def exchange(cls, *, by: ProposedBy = "user") -> Provenance:
        return cls(proposed_by=by, source_type="exchange")

    @classmethod
    def journal(cls, *, by: ProposedBy = "assistant") -> Provenance:
        return cls(proposed_by=by, source_type="journal")


# ---------------------------------------------------------------------------
# Affect
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Affect:
    """Dimensional + categorical affect annotation."""

    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    emotions: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.6

    def to_payload(self) -> dict[str, Any]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "dominance": self.dominance,
            "emotions": self.emotions,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Memory:
    """A stored memory as the API returns it."""

    id: int
    dyad_id: str
    text: str
    memory_type: str
    proposed_by: ProposedBy
    source_type: SourceType
    status: MemoryStatus
    consent_level: str
    context: str | None
    affect: Affect | None
    is_core: bool
    created_at: str
    updated_at: str | None
    review_after: str | None

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> Memory:
        a = data.get("affect")
        affect = None
        if a is not None:
            affect = Affect(
                valence=a.get("valence", 0.0),
                arousal=a.get("arousal", 0.0),
                dominance=a.get("dominance", 0.0),
                emotions=a.get("emotions", {}),
                confidence=a.get("confidence", 0.6),
            )
        return cls(
            id=data["id"],
            dyad_id=data["dyad_id"],
            text=data["text"],
            memory_type=data["memory_type"],
            proposed_by=data["proposed_by"],
            source_type=data["source_type"],
            status=data["status"],
            consent_level=data.get("consent_level", "medium"),
            context=data.get("context"),
            affect=affect,
            is_core=data.get("is_core", False),
            created_at=data["created_at"],
            updated_at=data.get("updated_at"),
            review_after=data.get("review_after"),
        )


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ScoreBreakdown:
    semantic: float
    affect: float
    relational: float
    recency: float


@dataclass(slots=True)
class SearchResult:
    id: int
    text: str
    memory_type: str
    proposed_by: ProposedBy
    source_type: SourceType
    score: float
    score_breakdown: ScoreBreakdown | None
    is_counterweight: bool
    created_at: str


@dataclass(slots=True)
class SearchResponse:
    results: list[SearchResult]
    routing_phase: str
    librarians_activated: int
    counterweights_injected: int
    latency_ms: float
    warnings: list[str]

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, idx: int) -> SearchResult:
        return self.results[idx]


# ---------------------------------------------------------------------------
# Thread / boot
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ThreadStart:
    boot_status: str
    boot_age_min: int
    journal_excerpt: str
    resonant_memories: list[dict[str, Any]]
    corrections: list[str]
    compliance: dict[str, Any]
    performance_ms: float


# ---------------------------------------------------------------------------
# Dyad
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DyadConsent:
    counterweights_enabled: bool = True
    hive_consume: bool = False
    hive_publish: bool = False
    persona_training: bool = False


@dataclass(slots=True)
class DyadInfo:
    id: str
    created_at: str
    last_activity_at: str | None
    total_memories: int
    consent: DyadConsent
