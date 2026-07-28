"""Consolidate + Ask DTOs — the wire shapes for `MemoryClient.consolidate()` / `.ask()`.

Authority: NET-NEW this phase (task brief: "New wire routes (consolidate/ask) are net-new — the
REST contract isn't fully pinned yet, that's tracked"). `mu-contracts` ships no `rest_schema` for
either verb yet (same documented gap `models/memory.py`/`models/recall.py` cite), so this SDK
declares its own pydantic mirror of the wire contract this phase pins at the demo server
(`demos/server/src/demo_server/schemas.py::ConsolidateRequestBody`/`ConsolidateResultOut`/
`AskRequestBody`/`AskResultOut`, read directly before writing this file — field-for-field parity).

Route: `POST /v1/memories/consolidate` (MTM->LTM DISTILL: invalidate-don't-delete SUPERSESSION +
bi-temporal SPO extraction) and `POST /v1/memories/ask` (MU's own SLM-powered synthesis over
recalled context — contrast with the raw ranked list `recall`/`search` return). Neither request body
carries tenancy (`namespace`/`user`/`session`) — resolved server-side from the auth identity, same
rule as every other verb in this package (see `models/memory.py` module docstring).

`ask()` requires the server to have a configured LLM/SLM; a heuristic-mode server's `/v1/memories/
ask` returns a NAMED 503 (`mu_local.errors.LlmNotConfiguredError` server-side), which
`error_mapping.raise_for_wire_error` already turns into a `ServiceUnavailableError` — no new SDK
error-mapping case needed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AskRequest",
    "AskResult",
    "ConsolidateRequest",
    "ConsolidateResult",
]


class ConsolidateRequest(BaseModel):
    """The wire request body for `POST /v1/memories/consolidate`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(default=50, ge=1, le=500)


class ConsolidateResult(BaseModel):
    """The DISTILL sweep receipt — genuine engine counts (`facts_extracted`/`added`/`superseded`),
    never an echo of the request. `superseded` is the invalidate-don't-delete SUPERSESSION count
    (the MemGC/Phi headline capability): prior conflicting facts marked `state=superseded` +
    `invalid_at` stamped, never deleted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    facts_extracted: int = Field(ge=0)
    added: int = Field(ge=0)
    superseded: int = Field(ge=0)
    generated_at: datetime


class AskRequest(BaseModel):
    """The wire request body for `POST /v1/memories/ask`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class AskResult(BaseModel):
    """The SLM-synthesized answer (never the raw recall items — use `recall()`/`search()` for
    retrieval without synthesis)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    answer: str
    generated_at: datetime
