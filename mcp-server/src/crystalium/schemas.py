"""Pydantic v2 models mirroring the 6 CRYSTALIUM JSON schemas.

Field names match the JSON schema property names exactly.
Cross-field validators enforce constraints that JSON Schema expresses via
'if/then' (e.g. content_ref required when layer='episodic').

Source of truth: schemas/*.v1.json (Draft 2020-12).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Provenance sub-object shared by Crystal, Skill, CommitRequest."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["human", "verified_agent", "unverified_agent", "environment"]
    author_agent: Optional[str] = None
    task_id: Optional[str] = None
    created_at: datetime


class Scope(BaseModel):
    """Scope triple: project + optional agent class visibility + sensitivity tag."""

    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1)
    agent_class_visibility: Optional[str] = None
    sensitivity_tag: Optional[str] = None
    # v1.6 (canonical project-key normalization, scope.py): populated on a WRITE
    # path only when the caller-supplied project differed from the canonical
    # (data-dir-derived) key. Preserves the original label for provenance;
    # never populated by recall (a read filter, not a write of record).
    project_raw: Optional[str] = None


class Temporal(BaseModel):
    """Bi-temporal validity window."""

    model_config = ConfigDict(extra="forbid")

    t_valid_from: datetime
    t_valid_to: Optional[datetime] = None
    superseded_by: Optional[str] = None


class Utility(BaseModel):
    """Utility signals for importance scoring and Dream eviction."""

    model_config = ConfigDict(extra="forbid")

    access_count: int = Field(ge=0)
    last_access: datetime
    outcome_success_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    novelty_at_write: float = Field(ge=0.0, le=1.0)


class MemoryDynamics(BaseModel):
    """Memory-dynamics signals consumed by later-wave algorithms.

    UNPOPULATED in v0.2.0 (schema-first migration, DECISION-1): no code reads or
    writes these yet. Bounds are intentionally open so W2 (EVB) and W4 (FSRS) can
    fix their ranges without a breaking schema bump.
    """

    model_config = ConfigDict(extra="forbid")

    stability: Optional[float] = None
    retrievability: Optional[float] = None
    difficulty: Optional[float] = None
    evb: Optional[float] = None
    prediction_error: Optional[float] = None


# ---------------------------------------------------------------------------
# Crystal (crystal.v1.json)
# ---------------------------------------------------------------------------

LayerLiteral = Literal["episodic", "semantic", "procedural", "execution"]
TierLiteral = Literal["T0", "T1", "T2", "T3"]
ValidationStateLiteral = Literal["validated", "unverified", "quarantined", "candidate"]
StatusLiteral = Literal["candidate", "active", "deprecated"]


class Crystal(BaseModel):
    """Mirrors crystal.v1.json.

    content_ref is REQUIRED when layer='episodic' (cross-field validator).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    layer: LayerLiteral
    content_ref: Optional[str] = Field(default=None, min_length=64, max_length=64)
    summary: str = Field(min_length=1, max_length=4096)
    embedding_ref: Optional[str] = None
    provenance: Provenance
    trust_tier: TierLiteral
    validation_state: ValidationStateLiteral
    scope: Scope
    temporal: Temporal
    utility: Utility
    status: StatusLiteral
    # v0.2.0 schema-first additions (DECISION-1) — UNPOPULATED; no code reads these yet.
    memory_dynamics: Optional[MemoryDynamics] = None
    tags: list[str] = Field(default_factory=list)
    protected: bool = False
    encoding_context: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def check_content_ref_required_for_episodic(self) -> "Crystal":
        """content_ref is REQUIRED when layer='episodic' (mirrors JSON Schema if/then)."""
        if self.layer == "episodic" and self.content_ref is None:
            raise ValueError(
                "content_ref is required when layer='episodic' "
                "(index→pointer→content pattern, MISSION.md §Index→pointer→content)."
            )
        return self

    @model_validator(mode="after")
    def check_content_ref_pattern(self) -> "Crystal":
        """content_ref must be a 64-char lowercase hex SHA-256 digest."""
        if self.content_ref is not None:
            import re

            if not re.fullmatch(r"[0-9a-f]{64}", self.content_ref):
                raise ValueError(
                    f"content_ref must be a 64-char lowercase hex SHA-256 digest, "
                    f"got: {self.content_ref!r}"
                )
        return self


# ---------------------------------------------------------------------------
# Skill input parameter
# ---------------------------------------------------------------------------


class SkillInput(BaseModel):
    """Typed parameter for a Skill verifier."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: Optional[str] = None
    required: bool = True


# ---------------------------------------------------------------------------
# Skill (skill.v1.json)
# ---------------------------------------------------------------------------


class Skill(BaseModel):
    """Mirrors skill.v1.json (procedural crystal frontmatter)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    capability_class: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=2048)
    language: Literal["python", "ts", "sql", "bash", "none"]
    inputs: list[SkillInput]
    verifier: str = Field(min_length=1)
    provenance: Provenance
    trust_tier: TierLiteral
    success_count: int = Field(ge=0)
    last_used: Optional[datetime] = None
    status: Literal["candidate", "shared", "deprecated"]
    activation_conditions: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# RecallRequest (recall-request.v1.json)
# ---------------------------------------------------------------------------


class RecallRequest(BaseModel):
    """Mirrors recall-request.v1.json."""

    model_config = ConfigDict(extra="forbid")

    scope: Scope
    query: str = Field(min_length=1, max_length=8192)
    k: int = Field(ge=1, le=100)
    layers: Optional[list[LayerLiteral]] = None  # None = all four layers


# ---------------------------------------------------------------------------
# RecallResult (recall-result.v1.json)
# ---------------------------------------------------------------------------


class CrystalSummary(BaseModel):
    """Summary record returned inside a RecallResult."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    layer: LayerLiteral
    summary: str = Field(min_length=1)
    trust_tier: TierLiteral
    validation_state: ValidationStateLiteral
    importance: float = Field(ge=0.0, le=1.0)
    last_access: datetime
    content_ref: Optional[str] = Field(default=None, min_length=64, max_length=64)
    score: Optional[float] = None


class SlotBreakdown(BaseModel):
    """Per-slot token counts."""

    model_config = ConfigDict(extra="forbid")

    executive: int = Field(ge=0)
    procedural: int = Field(ge=0)
    semantic: int = Field(ge=0)
    episodic: int = Field(ge=0)
    execution: int = Field(ge=0)
    buffer: int = Field(ge=0)


class Budget(BaseModel):
    """Working-set budget surfaced on every RecallResult (crystalium#36, DP-3c).

    Always present (never explain-gated) — an explain-only budget would
    recreate the exact discoverability failure issue #36 is about. `k` is
    documented as an upper bound *subject to* the fixed `total_cap` token
    budget (DP-3b: the budget itself never scales with k).
    """

    model_config = ConfigDict(extra="forbid")

    total_cap: int = Field(ge=0)
    slots: dict[str, int]
    k_requested: int = Field(ge=0)
    k_applied: int = Field(ge=0)
    #: Candidates dropped by the `k` gate only (DP-7) — NEVER folded with
    #: `evicted_count`, which keeps its existing meaning (token-budget drops).
    truncated_count: int = Field(ge=0)


class RecallResult(BaseModel):
    """Mirrors recall-result.v1.json."""

    model_config = ConfigDict(extra="forbid")

    records: list[CrystalSummary]
    slot_breakdown: SlotBreakdown
    total_tokens: int = Field(ge=0, le=3500)
    evicted_count: int = Field(ge=0)
    budget: Budget
    # v1.6 `recall --explain` (diagnosability). Populated only when the caller
    # opts in (explain=True); absent (None) otherwise, and callers should dump
    # with exclude_none=True so a normal recall's JSON shape is unchanged.
    # Free-form dict (not schema-pinned) — see aetheryte/retrieve.py::recall()
    # docstring for the exact shape.
    explain: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# CommitRequest (commit-request.v1.json)
# ---------------------------------------------------------------------------


class CommitRequest(BaseModel):
    """Mirrors commit-request.v1.json."""

    model_config = ConfigDict(extra="forbid")

    layer: LayerLiteral
    payload: dict[str, Any]  # Crystal payload without id
    provenance: Provenance
    caller_tier: TierLiteral
    force_promote: bool = False


# ---------------------------------------------------------------------------
# CommitResult (commit-result.v1.json) — oneOf committed | rejected
# ---------------------------------------------------------------------------


class CommitResultCommitted(BaseModel):
    """Successful commit result."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["committed"]
    id: str = Field(min_length=1)
    layer: LayerLiteral
    validation_state: ValidationStateLiteral
    importance: float = Field(ge=0.0, le=1.0)


class CommitResultRejected(BaseModel):
    """Rejected commit result with structured error."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["rejected"]
    reason_code: Literal[
        "TIER_VIOLATION",
        "TIER_CEILING",
        "VERIFIER_FAIL",
        "PROMOTION_GATE",
        "RATE_LIMIT",
        "PATH_ESCAPE",
        "PROMOTION_PENDING",
    ]
    detail: str = Field(min_length=1)


from typing import Union  # noqa: E402 — must come after literal definitions

CommitResult = Union[CommitResultCommitted, CommitResultRejected]
