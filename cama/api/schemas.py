"""Pydantic models for the CAMA v1 API.

Every enum is ``Literal[...]`` so unknown values are rejected at the API
boundary with HTTP 422. This is the architectural commitment that the
store never sees a memory_type / source_type / proposed_by value outside
the canonical set.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Canonical enums (closed sets, published in OpenAPI)
# ---------------------------------------------------------------------------

# proposed_by: who proposed the memory write
ProposedBy = Literal["user", "assistant", "system"]

# source_type: pipeline that produced the row
SourceType = Literal["teaching", "inference", "exchange", "journal"]

# memory_type: high-level shape of the memory
MemoryType = Literal[
    "experience",
    "teaching",
    "teaching_moment",
    "identity",
    "promise",
    "breakthrough",
    "correction",
    "emotional_turn",
    "recognition",
    "vulnerability",
    "caring",
    "boundary",
    "preference",
    "research",
    "building",
    "song",
    "dream",
    "journal",
    "insight",
    "pattern",
    "relationship",
    "exchange",
    "resistance",
]

# status: row lifecycle state
MemoryStatus = Literal["durable", "provisional", "expired", "rejected"]

# consent_level: how much weight the memory carries in retrieval
ConsentLevel = Literal["low", "medium", "high"]

# action: things consent tokens authorize
ConsentAction = Literal[
    "promote_to_durable", "delete_memory", "delete_dyad", "update_consent"
]


# ---------------------------------------------------------------------------
# Affect block, paired with every memory + every search query
# ---------------------------------------------------------------------------
class Affect(BaseModel):
    """Dimensional + categorical affect annotation."""

    model_config = ConfigDict(extra="forbid")

    valence: float = Field(ge=-1.0, le=1.0, default=0.0)
    arousal: float = Field(ge=-1.0, le=1.0, default=0.0)
    dominance: float = Field(ge=-1.0, le=1.0, default=0.0)
    emotions: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.6)


# ---------------------------------------------------------------------------
# Memory CRUD
# ---------------------------------------------------------------------------
class MemoryCreateRequest(BaseModel):
    """POST /v1/memories request body.

    The architectural commitment surfaces here: ``proposed_by`` and
    ``source_type`` are required, not optional. A request missing either
    one returns 422 with cama.violated_contract = ``provenance_required``.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=65536)
    memory_type: MemoryType
    proposed_by: ProposedBy
    source_type: SourceType
    context: str | None = Field(default=None, max_length=8192)
    affect: Affect | None = None
    consent_level: ConsentLevel = "medium"
    evidence: str | None = Field(default=None, max_length=8192)
    is_core: bool = False


class MemoryResponse(BaseModel):
    """The canonical memory record returned by every memories endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: int
    dyad_id: str
    text: str
    memory_type: MemoryType
    proposed_by: ProposedBy
    source_type: SourceType
    status: MemoryStatus
    consent_level: ConsentLevel
    context: str | None = None
    affect: Affect | None = None
    is_core: bool = False
    created_at: str
    updated_at: str | None = None
    review_after: str | None = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8192)
    limit: int = Field(ge=1, le=100, default=10)
    include_provisional: bool = False
    affect: Affect | None = None  # caller-supplied query affect, optional


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic: float
    affect: float
    relational: float
    recency: float


class SearchResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    text: str
    memory_type: MemoryType
    proposed_by: ProposedBy
    source_type: SourceType
    score: float
    score_breakdown: ScoreBreakdown | None = None
    is_counterweight: bool = False
    created_at: str


class RoutingMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    librarians_activated: int
    counterweights_injected: int
    latency_ms: float


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResultItem]
    routing: RoutingMeta
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Thread / boot
# ---------------------------------------------------------------------------
class ThreadStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str = Field(default="", max_length=8192)
    user_affect: Affect | None = None


class ThreadStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
class DyadConsent(BaseModel):
    """Per-dyad safety + sharing flags. The architectural opt-out point
    for the counterweight injection safety primitive."""

    model_config = ConfigDict(extra="forbid")

    counterweights_enabled: bool = True
    hive_consume: bool = False
    hive_publish: bool = False
    persona_training: bool = False


class DyadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    last_activity_at: str | None = None
    total_memories: int
    consent: DyadConsent


class ConsentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent: DyadConsent
    reason: str = Field(min_length=1, max_length=512)


class DyadDeleteManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted_at: str
    dyad_id: str
    counts: dict[str, int]
    deleted_ids_merkle_root: str
    audit_path: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "down"]
    db: Literal["ok", "down"]
    embedding_model: Literal["ok", "unavailable"]
    embedding_provider: str
    embedding_model_age_sec: int | None = None
    degraded: bool = False
    version: str
