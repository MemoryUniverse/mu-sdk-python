"""Memory DTOs — the wire shapes for `MemoryClient.add()` / `.search()`.

Authority: `api-mcp-surface-spec.md` Appendix A.1 ("frozen — `shared/api.py`"). `mu-contracts`
ships no `contracts/rest_schema` yet (its `contracts/__init__.py` is explicitly "SCAFFOLD ONLY" —
confirmed by reading the file directly, not taken on a tool's word), so this SDK declares its own
pydantic mirror of the documented shape (task brief's explicit fallback: "otherwise it declares its
own pydantic request/response models mirroring the wire contract").

`namespace` is deliberately ABSENT from `MemoryCreateRequest`: tenancy is derived server-side from
the resolved auth identity (`X-Demo-*` headers / bearer claims), never accepted as a client-supplied
body field (api-mcp-surface-spec.md §2.2) — the surface is never the oracle a probe can steer.
`MemoryResponse.namespace` is a **string** (the `to_prefix()` form), matching the Appendix A.1
citation literally — a different subsystem-spec era than the structured `Namespace` object used on
`RecallResult` (see `models/recall.py`); both are honored as documented rather than silently
unified.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from mu_contracts.domain.model.memory import Visibility
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MemoryCreateRequest", "MemoryListResponse", "MemoryResponse"]

ContentType = Literal["text", "code", "url", "image", "transcript"]
MemoryTierLiteral = Literal["stm", "mtm", "ltm"]
PolarityLiteral = Literal["positive", "negative"]


class MemoryCreateRequest(BaseModel):
    """`POST /memories` request body (api-mcp-surface-spec.md Appendix A.1, `api.py:103`).

    ``subject``/``predicate``/``object`` are carried here (not enumerated in the terse Appendix
    citation, but required by the `MemoriesApi.upsert(...)` method signature in both
    `api-sdk-mcp-surface-design.md` §5.6 and `api-mcp-surface-spec.md` §6 — a structured/triple
    memory has to reach the server through *some* body field, and `MemoryResponse` already carries
    `subject?/predicate?/object?` back out) — documented assumption, flagged rather than silently
    guessed past.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    content_type: ContentType = "text"
    tier: MemoryTierLiteral = "stm"
    importance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    visibility: Visibility
    local_memory_id: str | None = None
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class MemoryResponse(BaseModel):
    """`MemoryResponse` (`api.py:38`) — the full frozen field list, Appendix A.1."""

    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    content_type: ContentType
    tier: MemoryTierLiteral
    state: str
    importance_score: float = Field(ge=0.0, le=1.0)
    access_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    namespace: str  # to_prefix() string form (Appendix A.1 literal citation — see module docstring)
    metadata: dict[str, str] = Field(default_factory=dict)
    source: str | None = None
    speaker_kind: str | None = None
    speaker_id: str | None = None
    source_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    entity_id: str | None = None
    asserted_state: str | None = None
    gds_pagerank: float | None = None
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    object_kind: str | None = None
    object_type: str | None = None
    object_value: str | None = None
    polarity: PolarityLiteral | None = None
    predicate_cardinality: str | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expires_at: datetime | None = None
    last_seen: datetime | None = None
    mention_count: int = Field(default=1, ge=0)
    relevance_score: float = 0.0
    parent_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    content_hash: str = ""


class MemoryListResponse(BaseModel):
    """`MemoryListResponse` (`api.py:145`) — `GET /memories` response, the `.search()` shape."""

    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryResponse]
    total: int = Field(ge=0)
    tier: MemoryTierLiteral | None = None
