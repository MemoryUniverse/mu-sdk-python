"""Context (governed-transfer) DTOs — the wire shape for `MemoryClient.context.discover()`.

Authority: `ContextApi.discover(self, session_id: str) -> ContextIndexListView`
(`api-sdk-mcp-surface-design.md` §5.6, l.447) and the `ContextIndex` shape
(`governance-transfer-core-spec.md` §5, l.283-294). Route: `memory-universe://shared/{ws}/{ns}/
{session}/context` resource / `POST /v1/context/discover` (api-mcp-surface-spec.md §4.5, l.180).

**Scope note (tracked gap, not a silent stub — DEV-STANDARDS "no silent stubs that look
finished"):** the full governed-transfer FSM (`index`/`propose`/`inbox`/`decide`/`accept`/`revoke`,
`PROPOSED -> APPROVED -> DELIVERED -> ACCEPTED`, `governance-transfer-core-spec.md` §5) is a much
larger subsystem (`Grant`, `TransferClass`, `TransferPolicy`, `ShareableRef`, the revoke-cascade
saga) explicitly out of THIS phase's brief ("MemoryClient exposing add / search / recall /
context"). Only the read-only `discover` verb is implemented here, matching the single-verb
parallel structure of `add`/`search`/`recall`. `index`/`propose`/`inbox`/`decide`/`accept`/`revoke`
are NOT implemented; a caller reaching for them gets a clear `AttributeError`, not a silent no-op.

`ContextIndex.object_ref` (a `ShareableRef`, governance-transfer-core-spec.md §5) and `policy` are
therefore omitted from `ContextIndexView` below — they belong to the write-side FSM this phase does
not build. This is a deliberate, documented reduction of the full `ContextIndex` shape, not a guess.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ContextIndexListView", "ContextIndexView"]


class ContextIndexView(BaseModel):
    """The read-only projection of a `ContextIndex` (governance-transfer-core-spec.md:283-294)
    a peer session can discover. See module docstring for the fields deliberately omitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index_id: str = Field(min_length=1)
    transfer_class: str  # TransferClass enum value, carried as str here (see docstring)
    summary_ref: str | None = None
    symbolic_facts: tuple[str, ...] = ()
    allowed_memory_ids: tuple[str, ...] = ()
    created_at: datetime


class ContextIndexListView(BaseModel):
    """`ContextApi.discover(session_id) -> ContextIndexListView`
    (api-sdk-mcp-surface-design.md:447)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    indexes: list[ContextIndexView]
    generated_at: datetime
