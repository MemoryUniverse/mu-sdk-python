"""Central `Settings` tree for the SDK — the ONE place config is read (DEV-STANDARDS rule 3:
"No hardcoding — anything... everything flows from the central config").

Port-of-pattern: `mem0.MemoryClient.__init__` resolves `api_key`/`host` from arg-or-env
(`other_repos/mem0/mem0/client/main.py:64-71`, `:87-94`) — this module generalizes that single
inline resolution into a reusable `pydantic-settings` tree so every knob (timeouts, retries,
identity headers) is centrally configurable, never a scattered literal.

Env prefix ``MU_`` (nested via ``__``), e.g. ``MU_BASE_URL``, ``MU_API_KEY``,
``MU_IDENTITY__USER_ID``, ``MU_MAX_RETRIES`` — mirrors the ``MU_SURFACE__...`` convention used
server-side (api-mcp-surface-spec.md §7).
"""

from __future__ import annotations

from mu_contracts.contracts.defaults import (
    DEFAULT_CONSOLIDATE_LIMIT,
    DEFAULT_RECALL_LIMIT,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["SdkIdentity", "SdkSettings"]


class SdkIdentity(BaseModel):
    """The demo-auth identity quadruple (api-mcp-surface-spec.md §2.2: ``X-Demo-User-Id``,
    ``X-Demo-Workspace-Id``, ``X-Demo-Namespace-Id``, ``X-Demo-Session-Id``). These are the
    loopback-dev-only transport identity headers `DemoHeaderAuth` sends; the resolved `Namespace`
    on a request is ALWAYS derived server-side from these headers (or the bearer token's claims),
    never accepted as a client-supplied body field (Appendix A.1's `MemoryCreateRequest` and the
    recall request body both omit `namespace` — see `models/memory.py`, `models/recall.py`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str | None = None
    workspace_id: str | None = None
    namespace_id: str | None = None
    session_id: str | None = None

    def is_complete(self) -> bool:
        return all(
            (self.user_id, self.workspace_id, self.namespace_id, self.session_id),
        )


class SdkSettings(BaseSettings):
    """The one `Settings` tree the SDK reads (never `os.environ` scattered elsewhere).

    Nothing here is a magic number in calling code — `MemoryClient` and the transport/decorator
    stack all receive this object and read from it, never a hardcoded default inline.
    """

    model_config = SettingsConfigDict(
        env_prefix="MU_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    # -- transport --
    base_url: str = "https://api.memory-universe.dev"
    timeout_s: float = Field(default=30.0, gt=0)
    connect_timeout_s: float | None = Field(default=None, gt=0)

    # -- resilience (retry/backoff — DEV-STANDARDS "delegate to the adopted libs", tenacity here) --
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_base_s: float = Field(default=0.5, gt=0)
    backoff_max_s: float = Field(default=8.0, gt=0)

    # -- auth --
    api_key: str | None = None
    identity: SdkIdentity = SdkIdentity()

    # -- misc --
    user_agent: str = "mu-sdk-python/0.1.0"
    default_page_limit: int = Field(default=10, ge=1, le=100)
    # Independent from `default_page_limit`: `search()` is the simple ranked-list read (mem0
    # muscle-memory verb) while `recall()` is the richer multi-channel read (recall-service-
    # design.md §1.1) — they are tuned separately server-side, so the SDK gives each its own knob
    # rather than coupling them to one shared "list size" default.
    # Defaults to `mu_contracts`'s `DEFAULT_RECALL_LIMIT` — the wire-level default this knob backs
    # (CONFIG-AND-DATA-FIX-PLAN.md §1.1 Group D, C4) — not a second, independently-coincidental
    # `10` literal; see that module's docstring for the `02fbed9` bug this closes.
    default_recall_limit: int = Field(default=DEFAULT_RECALL_LIMIT, ge=1, le=100)
    # `consolidate()`'s sweep-size default — the SAME "add a settings field so no call site falls
    # back to a bare literal" fix `default_recall_limit` already got, for the OTHER Group-D verb
    # (`DEFAULT_CONSOLIDATE_LIMIT`, `mu_contracts.contracts.defaults`).
    default_consolidate_limit: int = Field(default=DEFAULT_CONSOLIDATE_LIMIT, ge=1, le=1000)
