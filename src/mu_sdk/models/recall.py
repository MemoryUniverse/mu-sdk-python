"""Recall DTOs — the wire shapes for `MemoryClient.recall()`.

Authority: `recall-service-design.md` §1.1 (l.78-125) — `RecallChannels`/`RecallMode`/`RecallQuery`/
`RecallItemView`/`RecallResult`. That spec's `RecallQuery` is the INTERNAL application-layer request
object (constructed server-side from the resolved `ClientScope` + the wire body); the WIRE request
this SDK sends is `RecallRequest` below — identical fields MINUS `namespace` (tenancy is
header/token-derived, never client-body-declared — same rule as `MemoryCreateRequest`, see
`models/memory.py`). The response, `RecallResult`, DOES carry the resolved `Namespace` back
(recall-service-design.md:116) since that is the server confirming what it resolved, not the client
asserting it.

Route: `mu-contracts/contracts` ships no `rest_schema` yet (scaffold-only — confirmed by reading
`contracts/__init__.py` directly). This SDK targets `POST /v1/memories/recall` (net-new route,
`/v1`-prefixed per api-mcp-surface-spec.md §2.1) with `RecallRequest` as the JSON body — a POST
(not GET) because `channels`/`mode`/`persona` are too rich for a querystring, matching the same
"POST a query object" shape already used for `POST /v1/context/discover` and `POST /v1/conflicts/
{id}/resolve`+body elsewhere in the same spec. Declared here per the task's explicit fallback
("declares its own pydantic request/response models mirroring the wire contract") since mu-contracts
has not pinned a route for this yet.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from mu_contracts.domain.events import DegradeReason
from mu_contracts.domain.model.memory import Namespace, Tier
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RecallChannels",
    "RecallItemView",
    "RecallMode",
    "RecallRequest",
    "RecallResult",
]


class RecallChannels(BaseModel):
    """Which channels a recall runs (recall-service-design.md:78-82). A degraded recall drops
    one; `RecallResult.channels_run` reports what actually ran."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stm: bool = True
    mtm: bool = True
    ltm: bool = True


class RecallMode(StrEnum):
    """recall-service-design.md:84-87."""

    RANKED = "ranked"  # default: full 3-channel ranked list
    ANSWER = "answer"  # recall -> LLM synthesis (ask path)
    INJECT = "inject"  # recall -> rendered additionalContext bundle


class RecallRequest(BaseModel):
    """The wire request body for `POST /v1/memories/recall`. Mirrors `RecallQuery`
    (recall-service-design.md:89-98) minus `namespace` (see module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    channels: RecallChannels = RecallChannels()
    mode: RecallMode = RecallMode.RANKED
    persona: str | None = None
    max_tokens: int | None = None
    correlation_id: str | None = None


class RecallItemView(BaseModel):
    """One ranked hit (recall-service-design.md:100-112). `content` is hydrated by id at render —
    never carried on a bus event; on the SDK this is simply the field the server returned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    content: str
    tier: Tier
    channel: str  # "stm" | "mtm" | "ltm" — provenance of the hit
    fused_score: float
    rerank_score: float | None = None
    is_floor: bool = False
    artifact_ref: str | None = None


class RecallResult(BaseModel):
    """The ranked read result (recall-service-design.md:114-124)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: Namespace
    items: list[RecallItemView]
    channels_run: RecallChannels
    degraded: DegradeReason | None = None
    generated_at: datetime

    @property
    def memory_ids(self) -> list[str]:
        """The content-free projection (recall-service-design.md:122-124)."""
        return [item.memory_id for item in self.items]
