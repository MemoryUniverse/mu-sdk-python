"""Recall DTOs — the wire shapes for `MemoryClient.recall()` and (indirectly, via
`ContextView.items`) `MemoryClient.build_context()`.

`RecallChannels`/`RecallItemView`/`RecallMode`/`RecallResult` are now CANONICAL, re-exported from
`mu_contracts.contracts.recall` (B0) per `SDK-BUILD-DECISIONS.md` Decision B: "`recall` ->
`RecallResult` (wire), a strict superset of the embedded `MemoryListView` — nothing embedded recall
returns is lost, adopted as-is." This module no longer declares its own duplicate copies — the
ones that used to live here (pre-B2) were byte-identical to B0's re-homed copies (confirmed by
reading both directly before this edit), so this is a pure re-home, not a reshaping.
`RecallItemView` is ALSO the single canonical per-hit item DTO `ContextView.items`
(`build_context`'s return shape, `mu_contracts.contracts.views`) uses — Decision B's cross-cutting
"unify the per-hit item DTO" section: one hit-item shape for both verbs, not two near-duplicates.

`RecallRequest` stays declared HERE (not moved to `mu-contracts`) — a request-only wire shape, not
a per-verb RETURN DTO (out of Decision B's scope), matching `mu_contracts.contracts.recall`'s own
docstring ("`RecallRequest` is deliberately NOT moved here... stays in `mu_sdk.models.recall`"). It
now imports `RecallChannels`/`RecallMode` from the canonical home instead of re-declaring them,
exactly as that docstring asks for.

Route: `POST /v1/memories/recall` (net-new, `/v1`-prefixed per api-mcp-surface-spec.md §2.1) — a
POST (not GET) because `channels`/`mode`/`persona` are too rich for a querystring, matching the
same "POST a query object" shape already used for `POST /v1/context/discover`/
`POST /v1/context/window` elsewhere in this package.
"""

from __future__ import annotations

from mu_contracts.contracts.recall import RecallChannels, RecallItemView, RecallMode, RecallResult
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RecallChannels",
    "RecallItemView",
    "RecallMode",
    "RecallRequest",
    "RecallResult",
]


class RecallRequest(BaseModel):
    """The wire request body for `POST /v1/memories/recall`. Mirrors the internal `RecallQuery`
    (recall-service-design.md:89-98) minus `namespace` (tenancy is header/token-derived, never
    client-body-declared — same rule as `MemoryCreateRequest`, see `models/memory.py`) and minus the
    private-plane `user`/`session`/`agent` fields (design §2.5 — those are validated/rejected by
    `MemoryClient.recall`'s plane-gating BEFORE this request is constructed, never smuggled into the
    wire body, exactly as `MemoryCreateRequest` already does not carry them either)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    channels: RecallChannels = RecallChannels()
    mode: RecallMode = RecallMode.RANKED
    persona: str | None = None
    max_tokens: int | None = None
    correlation_id: str | None = None
