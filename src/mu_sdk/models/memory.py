"""Memory DTOs — the wire shapes for `MemoryClient.add()` / `.get()` / `.share()` / `.search()`.

`MemoryResponse` and `MemoryWriteResult` are now CANONICAL, re-exported from
`mu_contracts.contracts` (B0: `mu-contracts/.../contracts/memory.py`, `.../contracts/views.py`) per
`SDK-BUILD-DECISIONS.md` Decision B ("Canonical return DTO per verb") and design §2.5 "Unified verb
surface" — this module no longer declares its OWN duplicate `MemoryResponse` class. The duplicate
that used to live here (pre-B2, `models/memory.py:58`) was byte-identical (confirmed by reading
B0's re-homed copy before this edit — same 37 fields, same `extra="forbid"`, nothing added/renamed/
dropped), so this is a pure re-home, not a reshaping. `ContentType`/`MemoryTierLiteral`/
`PolarityLiteral` move with it (needed for `MemoryResponse`'s own field types and for
`MemoryCreateRequest` below, which still declares its own request body — request DTOs are not part
of Decision B's return-DTO scope).

**`add()`'s return DTO changed from `MemoryResponse` to `MemoryWriteResult`** (Decision B: "`add`
is semantically a *write*; the honest return is a receipt of what happened, not a re-read of
state" — `MemoryResponse` lacks `promoted`/`tiers_written`, the two most informative write flags,
while `MemoryWriteResult` lacks the full row). This is a genuine, documented BREAKING CHANGE to the
SDK's own `MemoryClient.add()` Python return type — see that method's docstring in `client.py` for
the wire-transport derivation of `promoted`/`tiers_written` (not on the frozen `POST /memories`
response body, so approximated client-side, never silently invented). The wire HTTP contract
itself (Appendix A.1's `MemoryResponse` body) is UNTOUCHED — CANONICAL-CONTRACTS.md stays frozen,
unedited; only the SDK method's Python-level return shape changes. `get()`/`share()` (net-new this
phase) return the full-row `MemoryResponse` — Decision B's "full materialized row" bucket, distinct
from `add`'s "write receipt" bucket.

`MemoryCreateRequest` (request-only, never a per-verb RETURN DTO) and `MemoryListResponse` (the
`.search()` list wrapper — `search()` has no canonical-winner assignment; it is the mem0-muscle-
memory simple list verb, out of Decision B's `add`/`recall`/`get`/`build_context`/`share`/
`consolidate` scope) stay declared here, unchanged in shape.
"""

from __future__ import annotations

from mu_contracts.contracts.memory import (
    ContentType,
    MemoryResponse,
    MemoryTierLiteral,
    PolarityLiteral,
)
from mu_contracts.contracts.views import MemoryWriteResult
from mu_contracts.domain.model.memory import Visibility
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ContentType",
    "MemoryCreateRequest",
    "MemoryListResponse",
    "MemoryResponse",
    "MemoryTierLiteral",
    "MemoryWriteResult",
    "PolarityLiteral",
]


class MemoryCreateRequest(BaseModel):
    """`POST /memories` request body (api-mcp-surface-spec.md Appendix A.1, `api.py:103`).

    ``subject``/``predicate``/``object`` are carried here (not enumerated in the terse Appendix
    citation, but required by the `MemoriesApi.upsert(...)` method signature in both
    `api-sdk-mcp-surface-design.md` §5.6 and `api-mcp-surface-spec.md` §6 — a structured/triple
    memory has to reach the server through *some* body field, and `MemoryResponse` already carries
    `subject?/predicate?/object?` back out) — documented assumption, flagged rather than silently
    guessed past.

    `namespace` is deliberately ABSENT: tenancy is derived server-side from the resolved auth
    identity (`X-Demo-*` headers / bearer claims), never accepted as a client-supplied body field
    (api-mcp-surface-spec.md §2.2) — the surface is never the oracle a probe can steer.
    Private-plane identity (`user`/`session`/`agent`) is likewise never a body field here — see
    `MemoryClient.add`'s plane-gating docstring in `client.py`: those are validated and rejected
    BEFORE this request is even constructed, never smuggled into the wire body.
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


class MemoryListResponse(BaseModel):
    """`MemoryListResponse` (`api.py:145`) — `GET /memories` response, the `.search()` shape."""

    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryResponse]
    total: int = Field(ge=0)
    tier: MemoryTierLiteral | None = None
