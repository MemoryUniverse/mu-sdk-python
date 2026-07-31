"""`MemoryClient` — the async wire client (mu-local-and-sdk-spec.md §2.3; port-of-pattern
`mem0.MemoryClient`, `other_repos/mem0/mem0/client/main.py:24-105`).

Exposes `add` (write), `search` (simple ranked list, mem0 muscle-memory), `recall` (the
MU-canonical rich multi-channel read — `tier=` arg for tier-SCOPED recall), `get` (point read by
id, net-new this phase), `build_context` (the private-plane context-*window* helper, net-new this
phase — `LocalMemory.context`'s wire twin, design §2.5 REVIEW-2 FIX 1), `share` (the explicit
private->shared CROSSING verb, net-new this phase, design §2.5 REVIEW-2 FIX 4), `promote`/`demote`
(net-new this phase — honest `501`, build-queue item 5), `consolidate` (MTM->LTM DISTILL:
invalidate-don't-delete SUPERSESSION + bi-temporal SPO extraction), `ask` (MU's own SLM-powered
synthesis over recalled context), and `.context` (the `ContextApi` sub-client's read-only
`discover` — the SHARED-plane governed-transfer discovery sub-API, distinct from `build_context`,
see that method's docstring for the explicit "NOT the same verb" callout). No engine algorithm, no
store adapter — every verb that reaches the wire is one HTTP call through the `Transport` port,
wrapped by the retry/timeout/trace decorator stack (`mu_sdk.decorators`), with a typed error raised
via `mu_sdk.error_mapping` on any non-2xx response; `promote`/`demote` make no wire call at all
(nothing to call yet — see their own docstrings).

**R2 wire-contract reconciliation (SDK<->mu-engine-server, `2026-07-31-sdk-engine-server-design.md`
§2.5).** `add`/`recall`/`get`/`build_context`/`consolidate` each now build TWO possible wire shapes,
selected by `self._private_configured` (see below):

- **Private-plane path** (`self._private_configured` — `SdkConfig.mode in ("embedded",
  "local_server")`): builds the CANONICAL `mu_contracts.contracts.requests.*` model (R0) and
  projects it onto the route/field-set the REAL `mu-engine-server` accepts TODAY (verified against
  `mu_engine_server/routes/memories.py`+`.../context.py`, `mu_engine_server/schemas.py`, and
  `mu_engine/surface/facade.py::SurfaceFacade` directly, not guessed) — `POST /memories`
  {content,user,session}, `POST /v1/memories/recall` {text,user,session,tier,limit} (`tier` is a
  BODY field here, NOT a query param — the real server's route has no `tier=` query param at all,
  unlike the legacy path below), `GET /memories/{id}?user=&session=`, `POST /v1/context/window`
  {query,user,session,limit,max_chars}, `POST /v1/memories/consolidate` {user,session,limit}.
  Response parsing tries the canonical DTO the real server actually returns
  (`MemoryWriteResult`/`ConsolidateView` directly, per `response_model=`), falling back to the
  legacy full-row/`generated_at` shape ONLY for backends that have not caught up yet
  (`EmbeddedTransport`'s own fabricated payloads — see `_parse_add_response`/
  `_parse_consolidate_response` below for exactly which two shapes each tries and why).
- **Shared/legacy path** (`self._private_configured is False` — no `config=`, or
  `SdkConfig(mode="remote")`): BYTE-IDENTICAL to this class's pre-R2 behavior (the shape
  `tests/conformance_server/app.py` and every existing conformance/unit test already exercise) —
  `visibility`/`subject`/`predicate`/`object`/`tier`/`importance_score`/`metadata` on `add`,
  `?tier=` as a QUERY param on `recall`, `/v1/memories/{id}` (not `/memories/{id}`) on `get`,
  `{"text": ...}` (not `{"query": ...}`) on `build_context`. No behavior change for an existing
  caller that does not pass `config=`.

The canonical superset (R0) declares a FEW fields (`agent` on `add`/`recall`;
`channels`/`mode`/`persona`/`max_tokens`/`correlation_id` on `recall`; `tier`/`importance_score`/
`metadata`/`visibility`/`subject`/`predicate`/`object` on `add`) the real `mu-engine-server` has no
engine-side counterpart for YET (`SurfaceFacade.add`/`.recall` — read directly, both take only
`content|query, user, session[, tier, limit]`, nothing else). These stay on the PUBLIC method
signature (forward-compat / surface parity with `LocalMemory`) and are validated by
`validate_plane_fields`, but are NOT included in the private-plane wire dump — a documented,
tracked gap (R0's own docstring flags the identical gap from the request-contract side), never a
silent invention of server-side support that does not exist.

**Plane-gating (design §2.5 "Unified verb surface"; build-plan Stage B ruling 1, extended by R2).**
`add`/`recall`/`build_context`/`get`/`consolidate`/`share` accept the canonical superset
signature's plane-gated kwargs (`user`/`session`/`agent` — private-plane; `visibility`/`subject`/
`predicate`/`object` — shared-plane), validated by `mu_contracts.validation.plane_gate.
validate_plane_fields` — the SAME validator `LocalMemory` (mu-local, B1) calls, so a field supplied
for a plane this INSTANCE has not configured is REJECTED (`PlaneFieldRejectedError`), never
silently dropped or accepted-and-ignored.

`self._private_configured`/`self._shared_configured` are resolved ONCE, in `__init__`, from
`config.mode` (`"embedded"`/`"local_server"` -> a private plane is configured; `"remote"`, or a
populated `config.shared`, -> a shared plane is configured) — the per-instance state
`mu_sdk.config.SdkConfig`'s own docstring names as the follow-on the module-level
`_PRIVATE_PLANE_CONFIGURED`/`_SHARED_PLANE_CONFIGURED` constants below were left for ("D1 stores
this value on the client for a future stage to wire; it has no effect yet"). R2 is that stage: a
caller who never passes `config=` (every existing `settings=`/`transport=` construction, including
every current unit/integration test) gets EXACTLY the old module-level constants below
(`private_configured=False, shared_configured=True` — "unconditionally the SHARED-plane wire
client") as its per-instance default, so nothing that already worked changes; a caller who DOES
pass `config=SdkConfig(mode="embedded"|"local_server", ...)` now genuinely gains a private plane
(`user=`/`session=`/`agent=` accepted, `visibility=`/etc. rejected), and `mode="remote"` genuinely
gains a shared plane exactly as `config="remote"` implies — the dispatch this task's own
verification requires (`client.add(content, user=, session=)` succeeding over `mode="local_server"`
is impossible without this).

Cancellation (DEV-STANDARDS rule 1): every await here is a direct `await` with no surrounding
`except Exception`/`except BaseException` — `asyncio.CancelledError` propagates untouched through
this class, through the decorator stack (see `decorators.py` docstring), and through
`HttpxTransport` (a bare `httpx` await). `aclose()` is idempotent and safe to call from a
`finally` block on cancellation.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from mu_contracts.contracts.requests import AddRequest as _CanonicalAddRequest
from mu_contracts.contracts.requests import ConsolidateRequest as _CanonicalConsolidateRequest
from mu_contracts.contracts.requests import ContextWindowRequest as _CanonicalContextWindowRequest
from mu_contracts.contracts.requests import GetRequest as _CanonicalGetRequest
from mu_contracts.contracts.requests import RecallRequest as _CanonicalRecallRequest
from mu_contracts.contracts.views import ConsolidateView
from mu_contracts.domain.model.memory import Visibility
from mu_contracts.validation.plane_gate import validate_plane_fields
from pydantic import ValidationError as _PydanticValidationError

from mu_sdk.auth import BearerAuth, SdkAuth, resolve_auth
from mu_sdk.config import SdkConfig
from mu_sdk.decorators import with_retry, with_timeout, with_trace
from mu_sdk.error_mapping import raise_for_wire_error
from mu_sdk.errors import AuthenticationError, NotFoundError, SurfaceVerbNotImplementedError
from mu_sdk.models.consolidate import AskRequest, AskResult
from mu_sdk.models.consolidate import ConsolidateRequest as _LegacyConsolidateRequest
from mu_sdk.models.consolidate import ConsolidateResult as _LegacyConsolidateResult
from mu_sdk.models.context import ContextIndexListView, ContextView
from mu_sdk.models.memory import MemoryCreateRequest as _LegacyAddRequest
from mu_sdk.models.memory import (
    MemoryListResponse,
    MemoryResponse,
    MemoryWriteResult,
)
from mu_sdk.models.recall import RecallChannels, RecallMode, RecallResult
from mu_sdk.models.recall import RecallRequest as _LegacyRecallRequest
from mu_sdk.settings import SdkSettings
from mu_sdk.transport import (
    EmbeddedTransport,
    HttpxTransport,
    LocalServerTransport,
    NullAuth,
    RemoteTransport,
    Transport,
    TransportResponse,
    load_engine_server_token,
)

__all__ = ["ContextApi", "MemoryClient"]

_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"  # api-mcp-surface-spec.md §2.3

# Legacy, PRE-R2 module-level plane defaults — now used ONLY as the per-instance fallback for a
# `MemoryClient` constructed WITHOUT `config=` (every existing `settings=`/`transport=` caller,
# see `MemoryClient.__init__`/module docstring). `config=SdkConfig(mode=...)` resolves its own,
# genuinely mode-dependent `_private_configured`/`_shared_configured` instead (R2) — these two
# constants no longer govern every instance unconditionally, only the no-`config=` legacy one.
_PRIVATE_PLANE_CONFIGURED = False
_SHARED_PLANE_CONFIGURED = True


def _resolve_from_config(
    config: SdkConfig,
    *,
    settings: SdkSettings | None,
    transport: Transport | None,
    auth: SdkAuth | None,
) -> tuple[SdkSettings, Transport, SdkAuth]:
    """`MemoryClient(config=...)`'s transport-selection logic (build-plan §5 D1, design §3/§1.2) —
    the ONE place `SdkConfig.mode` turns into a real `(settings, transport, auth)` triple. An
    explicit `settings=`/`transport=`/`auth=` argument always wins over what `config=` would have
    produced (module docstring on `MemoryClient.__init__`) — this function only fills the gaps.

    `config.shared` (dual-plane, design §4) is deliberately NOT consulted here for
    transport/auth SELECTION (a dual-plane client still has exactly one PRIVATE transport — the
    shared plane, when populated, only ADDS accepted shared-plane fields on the wire-shape
    dispatch in `MemoryClient.__init__`, see that method's own plane-resolution comment) — no
    second transport is constructed here."""
    resolved_settings = settings or SdkSettings(
        base_url=config.endpoint or SdkSettings.model_fields["base_url"].get_default(),
        timeout_s=config.timeout_s,
    )

    resolved_transport: Transport
    if transport is not None:
        resolved_transport = transport
    elif config.mode == "embedded":
        resolved_transport = EmbeddedTransport(config)
    elif config.mode == "local_server":
        resolved_transport = LocalServerTransport(resolved_settings)
    else:  # config.mode == "remote" (Literal — SdkConfig only allows these 3 values)
        resolved_transport = RemoteTransport(resolved_settings)

    resolved_auth: SdkAuth
    if auth is not None:
        resolved_auth = auth
    elif config.mode == "embedded":
        resolved_auth = NullAuth()
    elif config.auth is not None:
        resolved_auth = config.auth
    elif config.mode == "local_server":
        # design §1.2 FIX 4: no auth= given for local_server -> auto-load the per-process bearer
        # token `make up` mints to disk. `load_engine_server_token` raises the NAMED
        # `EngineServerTokenMissingError` (never a bare exception) when it is absent.
        resolved_auth = BearerAuth(load_engine_server_token())
    else:  # config.mode == "remote" with no auth= — SdkConfig's own validator already forbids
        #      constructing this SdkConfig in the first place; kept as defense-in-depth so this
        #      function fails loud even if a caller somehow bypasses that validator.
        raise AuthenticationError("SdkConfig(mode='remote') requires auth=... (design §3).")

    return resolved_settings, resolved_transport, resolved_auth


def _parse_add_response(payload: Any) -> MemoryWriteResult:
    """`POST /memories`'s response shape genuinely differs by backend TODAY (R2 finding, a real,
    tracked gap — not invented): the real `mu-engine-server` (`routes/memories.py`,
    `response_model=MemoryWriteResult`) already returns the canonical write RECEIPT directly
    (`SurfaceFacade.add` returns `MemoryWriteResult`, the route re-serializes it verbatim). The
    legacy/shared conformance path (`tests/conformance_server/app.py::upsert_memory`) and
    `EmbeddedTransport`'s own fabricated payload (`mu_sdk.transport.EmbeddedTransport._add`, NOT
    edited by this task) both still return the frozen FULL-ROW `MemoryResponse` shape (Appendix
    A.1) that predates Decision B's "add returns a receipt" ruling. Try the canonical receipt shape
    first (the REAL server's actual shape); fall back to the full-row remap (`promoted`/
    `tiers_written` approximated from the returned `tier`, byte-identical to this method's own
    pre-R2 behavior) — a real parse attempt against one of the two known, verified shapes, never a
    blind guess."""
    try:
        return MemoryWriteResult.model_validate(payload)
    except _PydanticValidationError:
        wire_response = MemoryResponse.model_validate(payload)
        return MemoryWriteResult(
            memory_id=wire_response.id,
            content_hash=wire_response.content_hash,
            promoted=wire_response.tier != "stm",
            tiers_written=(wire_response.tier,),
            namespace=wire_response.namespace,
        )


def _parse_consolidate_response(payload: Any) -> ConsolidateView:
    """`POST /v1/memories/consolidate`'s response shape has the SAME two-shapes-today split as
    `_parse_add_response` above, for the SAME reason: the real `mu-engine-server`
    (`routes/memories.py`, `response_model=ConsolidateView`) already returns the canonical
    `ConsolidateView` (`facts_extracted`/`added`/`superseded`/`noop`) directly — R0's own docstring
    names this the "A4 winner" reconciliation this function performs. `EmbeddedTransport.
    _consolidate` (NOT edited by this task) and the legacy conformance path both still return the
    OLDER `{facts_extracted, added, superseded, generated_at}` shape (no `noop`, an extra
    `generated_at` `ConsolidateView` forbids) — caught here and remapped, `noop=0` since that
    legacy shape never tracked NOOP actions at all (never silently reinterpreted as one of the
    other three counts)."""
    try:
        return ConsolidateView.model_validate(payload)
    except _PydanticValidationError:
        legacy = _LegacyConsolidateResult.model_validate(payload)
        return ConsolidateView(
            facts_extracted=legacy.facts_extracted,
            added=legacy.added,
            superseded=legacy.superseded,
            noop=0,
        )


class ContextApi:
    """The `context` sub-client — `discover` only this phase (see `mu_sdk.models.context`
    module docstring for the tracked-gap rationale on `index`/`propose`/`inbox`/`decide`/
    `accept`/`revoke`)."""

    def __init__(self, client: MemoryClient) -> None:
        self._client = client

    async def discover(self, session_id: str) -> ContextIndexListView:
        """`POST /v1/context/discover` (api-mcp-surface-spec.md §4.5, l.180); the SDK method
        shape is `ContextApi.discover(session_id) -> ContextIndexListView`
        (api-sdk-mcp-surface-design.md:447)."""
        response = await self._client._execute(
            "POST",
            "/v1/context/discover",
            json_body={"session_id": session_id},
        )
        return ContextIndexListView.model_validate(response.json_body)


class MemoryClient:
    """Async SDK. Constructs with a `SdkSettings` tree (api_key / demo identity / base_url /
    timeouts / retries — never a hardcoded literal, DEV-STANDARDS rule 3) and an optional
    injected `Transport` (defaults to `HttpxTransport`, swappable for isolated unit tests of the
    error-mapping/retry logic — never mocked in integration tests).

    **Stage D (build-plan §5 D1) — `config=` transport selection (design §3).** Pass an
    `SdkConfig` and the constructor picks the transport FOR you from `config.mode`
    (`EmbeddedTransport`/`LocalServerTransport`/`RemoteTransport`, `mu_sdk.transport`) — the
    "byte-identical code, config picks the transport" guarantee (design §1/§6).

    **R2 — per-instance plane resolution (see module docstring's "Plane-gating" section).**
    `config=`, when given, ALSO resolves this instance's `_private_configured`/
    `_shared_configured` flags every verb body's `validate_plane_fields` call and private/legacy
    wire-shape branch reads: `mode in ("embedded", "local_server")` -> a private plane is
    configured; `mode == "remote"` or a populated `config.shared` -> a shared plane is configured.
    No `config=` (the pre-R2 construction path every existing test still uses) resolves to the
    OLD module-level constants (`_PRIVATE_PLANE_CONFIGURED=False, _SHARED_PLANE_CONFIGURED=True`)
    — unchanged behavior for every caller that predates this task.

    `settings=`/`transport=`/`auth=` remain fully independent, pre-Stage-D construction knobs
    (used directly by every existing unit/integration test in this package, none of which pass
    `config=`) — passing `config=` only FILLS IN whichever of the three you did not pass
    explicitly (an explicit `settings=`/`transport=`/`auth=` always wins over what `config=` would
    have resolved, the same "explicit arg beats config-derived default" precedent
    `mu_engine_server.auth.get_token_path` already uses for its own arg > env > default order)."""

    def __init__(
        self,
        *,
        config: SdkConfig | None = None,
        settings: SdkSettings | None = None,
        transport: Transport | None = None,
        auth: SdkAuth | None = None,
    ) -> None:
        if config is not None:
            settings, transport, auth = _resolve_from_config(
                config, settings=settings, transport=transport, auth=auth
            )
            # R2: a real private/shared plane, genuinely mode-dependent (module + class docstring).
            self._private_configured = config.mode in ("embedded", "local_server")
            self._shared_configured = config.mode == "remote" or config.shared is not None
        else:
            # Pre-R2 legacy default — unchanged for every caller that does not pass `config=`.
            self._private_configured = _PRIVATE_PLANE_CONFIGURED
            self._shared_configured = _SHARED_PLANE_CONFIGURED
        self._config = config
        self._settings = settings or SdkSettings()
        self._auth = auth or resolve_auth(self._settings)
        self._transport = transport or HttpxTransport(self._settings)
        self._owns_transport = transport is None

        # The overall wall-clock ceiling wraps every retry of one logical call (decorators.py's
        # composition-order docstring): each attempt gets the full per-attempt budget, so the
        # ceiling is generous rather than `timeout_s` alone (which would starve retries).
        overall_timeout_s = self._settings.timeout_s * (self._settings.max_retries + 1) + (
            self._settings.backoff_max_s
        )
        self._execute = with_trace()(
            with_timeout(overall_timeout_s)(
                with_retry(
                    max_retries=self._settings.max_retries,
                    backoff_base_s=self._settings.backoff_base_s,
                    backoff_max_s=self._settings.backoff_max_s,
                )(self._raw_request)
            )
        )
        self._context_api = ContextApi(self)

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> TransportResponse:
        """The ONE request choke-point every public verb funnels through (see module + decorators
        docstrings). Merges auth headers under any caller-supplied header (trace headers win over
        neither — they are disjoint keys).

        `raise_for_wire_error` is called HERE, inside the retried scope (`with_retry` wraps this
        function directly), so a 429/503 response raises a `TransientSdkError` that `with_retry`
        can actually see and retry — raising it after `_execute` returns would be after every
        retry is already exhausted. Every public verb's `_execute(...)` return is therefore
        guaranteed 2xx; no verb needs to call `raise_for_wire_error` itself."""
        merged_headers = {**self._auth.headers(), **(headers or {})}
        response = await self._transport.request(
            method,
            path,
            params=params,
            json_body=json_body,
            headers=merged_headers,
        )
        raise_for_wire_error(response)
        return response

    @property
    def context(self) -> ContextApi:
        return self._context_api

    # ---- write ----

    async def add(
        self,
        content: str,
        *,
        visibility: Visibility | None = None,
        tier: str | None = None,
        importance_score: float | None = None,
        idempotency_key: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,  # matches the frozen wire field name (Appendix A.1) exactly
        metadata: dict[str, str] | None = None,
        user: str | None = None,
        session: str | None = None,
        agent: str | None = None,
    ) -> MemoryWriteResult:
        """`POST /memories` (api-mcp-surface-spec.md §4.3; Appendix A.1 for the legacy/shared wire
        shape; design §2.5 for the private-plane one — see module docstring's "R2 wire-contract
        reconciliation" section for exactly which fields reach which wire).

        `visibility`/`tier`/`importance_score` now default to `None` (were `Visibility.SHARED`/
        `"stm"`/`0.5`) so the canonical superset's "omitted -> not sent, plane/backend decides"
        discipline (`mu_contracts.contracts.requests.AddRequest`'s own docstring) applies uniformly
        — on the SHARED/legacy path this method still resolves an omitted `visibility` to
        `Visibility.SHARED` internally (byte-identical outgoing wire body to before); an omitted
        `tier`/`importance_score` is simply not sent (the legacy conformance server already
        defaults them itself, `body.get("tier", "stm")`, so this is not an observable behavior
        change either — verified against that server directly before writing this).

        Shared `POST /memories` rejects `visibility=PRIVATE` server-side (`app.py:1690`) — the
        SDK has no private-to-shared leak path; a PRIVATE write raises `PrivateDataRejectedError`
        (`mu_sdk.error_mapping`), it is never silently coerced to SHARED.

        `idempotency_key`, when given, is sent as the `Idempotency-Key` HEADER (api-mcp-
        surface-spec.md §2.3 write-idempotency contract) — never duplicated into the JSON body,
        on EITHER wire path.

        **Return DTO is `MemoryWriteResult`** (design §2.5 "Return DTOs", `SDK-BUILD-DECISIONS.md`
        Decision B) — see `_parse_add_response`'s own docstring for the two response shapes this
        method now transparently accepts.

        `user`/`session`/`agent` (design §2.5's private-plane fields) and `visibility`/`subject`/
        `predicate`/`object` (shared-plane fields) are validated via `validate_plane_fields`
        against THIS INSTANCE's `_private_configured`/`_shared_configured` (module docstring) —
        rejected, never silently dropped, when the field's plane is not configured.
        """
        validate_plane_fields(
            {
                "user": user,
                "session": session,
                "agent": agent,
                "visibility": visibility,
                "subject": subject,
                "predicate": predicate,
                "object": object,
            },
            private_configured=self._private_configured,
            shared_configured=self._shared_configured,
        )
        headers = {_IDEMPOTENCY_KEY_HEADER: idempotency_key} if idempotency_key else None

        if self._private_configured:
            # Canonical superset request (R0) — but the REAL mu-engine-server's own schema/facade
            # (verified directly: mu_engine_server/schemas.py::AddRequest,
            # mu_engine/surface/facade.py::SurfaceFacade.add) accept only content/user/session
            # TODAY. `agent`/`visibility`/`subject`/`predicate`/`object`/`tier`/`importance_score`/
            # `metadata` have no engine-side counterpart on this plane yet — constructed on the
            # canonical model (so plane-gating/typing sees the full superset) but excluded from
            # THIS wire dump via `include=`, a documented, tracked gap (R0's own docstring flags
            # the identical gap from the request-contract side), never a silent invention of
            # server-side support that does not exist.
            request = _CanonicalAddRequest(
                content=content,
                user=user,
                session=session,
                agent=agent,
                visibility=visibility,
                subject=subject,
                predicate=predicate,
                object=object,
                tier=tier,  # type: ignore[arg-type]  # validated by AddRequest's Literal
                importance_score=importance_score,
                metadata=metadata,
            )
            body = request.model_dump(
                mode="json", include={"content", "user", "session"}, exclude_none=True
            )
            response = await self._execute(
                "POST", "/memories", json_body=body, headers=headers
            )
            return _parse_add_response(response.json_body)

        # ---- shared/legacy wire path — byte-identical to this method's pre-R2 behavior ----
        effective_visibility = visibility if visibility is not None else Visibility.SHARED
        legacy_request = _LegacyAddRequest(
            content=content,
            visibility=effective_visibility,
            tier=tier or "stm",  # type: ignore[arg-type]  # validated by MemoryCreateRequest's Literal
            importance_score=importance_score if importance_score is not None else 0.5,
            subject=subject,
            predicate=predicate,
            object=object,
            metadata=metadata or {},
        )
        response = await self._execute(
            "POST",
            "/memories",
            json_body=legacy_request.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )
        return _parse_add_response(response.json_body)

    # ---- read ----

    async def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        tier: str | None = None,
    ) -> MemoryListResponse:
        """`GET /memories?query=&limit=&tier=` — the simple ranked-list read (mem0 muscle-memory
        verb name; api-mcp-surface-spec.md §4.3 `GET /memories`/`GET /memories/recall` (local)
        row). For channel/mode/persona control use `.recall()` instead.

        Untouched by R2 (out of the 5-verb reconciliation scope; the real `mu-engine-server` has
        no `GET /memories` search route yet — a separate, pre-existing gap)."""
        params: dict[str, Any] = {
            "query": query,
            "limit": limit if limit is not None else self._settings.default_page_limit,
        }
        if tier is not None:
            params["tier"] = tier
        response = await self._execute("GET", "/memories", params=params)
        return MemoryListResponse.model_validate(response.json_body)

    async def recall(
        self,
        text: str,
        *,
        limit: int | None = None,
        channels: RecallChannels | None = None,
        mode: RecallMode = RecallMode.RANKED,
        persona: str | None = None,
        max_tokens: int | None = None,
        correlation_id: str | None = None,
        tier: str | None = None,
        user: str | None = None,
        session: str | None = None,
        agent: str | None = None,
    ) -> RecallResult:
        """`POST /v1/memories/recall` — the MU-canonical rich multi-channel read
        (recall-service-design.md §1.1; see `mu_sdk.models.recall` module docstring for the
        wire-route rationale). Tenancy (`namespace`) is resolved server-side from the auth
        identity on the shared/legacy path, or from `user=`/`session=` on the private-plane path.

        **Private-plane path** (`self._private_configured`): `tier` travels as a canonical
        `RecallRequest` BODY field (`mu_engine_server/schemas.py::RecallRequest.tier` — the real
        server's route has NO `?tier=` query param at all, verified directly against
        `mu_engine_server/routes/memories.py::recall_memories`), narrowing to exactly one real
        channel. `channels`/`mode`/`persona`/`max_tokens`/`correlation_id` are accepted on this
        signature for forward-compat but have no engine-side counterpart yet (same documented gap
        as `add()`'s `tier`/`importance_score`/`metadata`) — not sent on this wire.

        **Shared/legacy path**: byte-identical to this method's pre-R2 behavior — `tier`, when
        given, is sent as the `?tier=` QUERY param (not a body field) and always wins server-side
        over `channels`; `None` (the default) leaves channel selection to `channels`/`mode` as
        before, no behavior change for an existing caller that doesn't pass `tier`.

        `user`/`session`/`agent` (design §2.5's private-plane fields) are validated via
        `validate_plane_fields` against this instance's configured plane(s) — see `add()`'s
        docstring / the module docstring's "Plane-gating" section."""
        validate_plane_fields(
            {"user": user, "session": session, "agent": agent},
            private_configured=self._private_configured,
            shared_configured=self._shared_configured,
        )
        effective_limit = limit if limit is not None else self._settings.default_recall_limit
        effective_channels = channels or RecallChannels()

        if self._private_configured:
            request = _CanonicalRecallRequest(
                text=text,
                user=user,
                session=session,
                agent=agent,
                tier=tier,  # type: ignore[arg-type]  # validated by RecallRequest's Literal
                limit=effective_limit,
                channels=effective_channels,
                mode=mode,
                persona=persona,
                max_tokens=max_tokens,
                correlation_id=correlation_id,
            )
            body = request.model_dump(
                mode="json",
                include={"text", "user", "session", "tier", "limit"},
                exclude_none=True,
            )
            response = await self._execute("POST", "/v1/memories/recall", json_body=body)
            return RecallResult.model_validate(response.json_body)

        # ---- shared/legacy wire path — byte-identical to this method's pre-R2 behavior ----
        legacy_request = _LegacyRecallRequest(
            text=text,
            limit=effective_limit,
            channels=effective_channels,
            mode=mode,
            persona=persona,
            max_tokens=max_tokens,
            correlation_id=correlation_id,
        )
        params = {"tier": tier} if tier is not None else None
        response = await self._execute(
            "POST",
            "/v1/memories/recall",
            params=params,
            json_body=legacy_request.model_dump(mode="json", exclude_none=True),
        )
        return RecallResult.model_validate(response.json_body)

    async def get(
        self,
        memory_id: str,
        *,
        user: str | None = None,
        session: str | None = None,
    ) -> MemoryResponse | None:
        """Point-get one memory by id. **Private-plane path** (`self._private_configured`):
        `GET /memories/{memory_id}?user=&session=` — the REAL `mu-engine-server`'s actual route
        (`mu_engine_server/routes/memories.py::get_memory`, verified directly), `user=`/`session=`
        as query params, never a body. **Shared/legacy path**: `GET /v1/memories/{memory_id}`, no
        query params — byte-identical to this method's pre-R2 behavior (the placeholder route
        `tests/conformance_server/app.py` implements; no frozen route was pinned for a single-
        memory GET in Appendix A.1 when this verb was first added).

        `user`/`session` are validated via `validate_plane_fields` exactly like every other
        private-plane field on this class — see `add()`'s docstring.

        Returns `None` on a 404 (not-found) rather than raising `NotFoundError` — a point-get miss
        is a perfectly normal outcome, mirroring `LocalMemory.get`'s `| None` miss signal
        (`SDK-BUILD-DECISIONS.md` Decision B: "keep the `| None` miss signal that embedded `get`
        already returns")."""
        validate_plane_fields(
            {"user": user, "session": session},
            private_configured=self._private_configured,
            shared_configured=self._shared_configured,
        )
        # Canonical shape-validation (R0) — `GetRequest` is projected onto path/query params by
        # design (its own docstring: "construct it, then project its fields onto whichever
        # transport-specific locations the calling code needs"), never dumped as a JSON body.
        _CanonicalGetRequest(memory_id=memory_id, user=user, session=session)

        if self._private_configured:
            path = f"/memories/{memory_id}"
            params: dict[str, Any] | None = {
                key: value
                for key, value in {"user": user, "session": session}.items()
                if value is not None
            } or None
        else:
            path = f"/v1/memories/{memory_id}"
            params = None

        try:
            response = await self._execute("GET", path, params=params)
        except NotFoundError:
            return None
        return MemoryResponse.model_validate(response.json_body)

    async def build_context(
        self,
        text: str,
        *,
        user: str | None = None,
        session: str | None = None,
        limit: int | None = None,
        max_chars: int | None = None,
    ) -> ContextView:
        """`POST /v1/context/window` (design §2.5 REVIEW-2 FIX 1) — `LocalMemory.context(...)`'s
        WIRE TWIN: the PRIVATE-plane context-*WINDOW* helper (deterministic recall + render, NO
        LLM synthesis, `mu_local.views.ContextView`'s `SDK-BUILD-DECISIONS.md` Decision B
        rationale).

        **Private-plane path** (`self._private_configured`): the query-text field is named
        `query` on the wire — the REAL `mu-engine-server`'s actual field name
        (`mu_engine_server/schemas.py::ContextWindowRequest.query`, verified directly; also R0's
        own "Field-name decisions" ruling: "`build_context`'s query text is named `query`, not
        `text`... the ONE genuine cross-field-name bug this task's context pack flags"). This
        method's own PUBLIC parameter stays named `text` (API stability / consistency with
        `recall(text, ...)`) — only the WIRE body field changes.

        **Shared/legacy path**: byte-identical to this method's pre-R2 behavior — `{"text": ...}`
        on the wire, matching `tests/conformance_server/app.py`'s placeholder route.

        **Not the same verb as `.context.discover(...)`** (`ContextApi`, above) — REVIEW-2 FIX 1
        (BLOCKER) is explicit that collapsing these two is unbuildable: they take different
        inputs, return different DTOs (`ContextView` here vs. `ContextIndexListView` there), and
        live on different planes (`.context.discover` is the SHARED-plane governed-transfer
        discovery sub-API, plane-gated to a populated `shared=`/`mode="remote"`; this method is the
        PRIVATE-plane window helper). The name collision is why this method is `build_context`, not
        `context` — `.context` already means the shared sub-client on this class and stays
        untouched by this addition.

        `user`/`session` are the private-plane fields this verb inherently needs (mirrors
        `LocalMemory.context`'s own signature) — plane-gated exactly like `add`/`recall`'s
        `user`/`session`/`agent`."""
        validate_plane_fields(
            {"user": user, "session": session},
            private_configured=self._private_configured,
            shared_configured=self._shared_configured,
        )
        effective_limit = limit if limit is not None else self._settings.default_recall_limit

        if self._private_configured:
            request = _CanonicalContextWindowRequest(
                query=text,
                user=user,
                session=session,
                limit=effective_limit,
                max_chars=max_chars,
            )
            body = request.model_dump(mode="json", exclude_none=True)
            response = await self._execute("POST", "/v1/context/window", json_body=body)
            return ContextView.model_validate(response.json_body)

        # ---- shared/legacy wire path — byte-identical to this method's pre-R2 behavior ----
        request_body: dict[str, Any] = {"text": text, "limit": effective_limit}
        if max_chars is not None:
            request_body["max_chars"] = max_chars
        response = await self._execute("POST", "/v1/context/window", json_body=request_body)
        return ContextView.model_validate(response.json_body)

    async def share(self, memory_id: str, *, visibility: Visibility) -> MemoryResponse:
        """`POST /v1/memories/{memory_id}/share` (design §2.5 REVIEW-2 FIX 4; §13 item 3) — the
        explicit private->shared CROSSING verb: *"There is no auto-bridge in `mu-local`... the
        crossing is always an explicit SDK write"* (`mu-local-and-sdk-spec.md` §4.3, preserved
        verbatim by design §4's dual-plane worked example, which calls this exact method:
        `mem.share(result.memory_id, visibility="shared")`).

        Untouched by R2 (out of the 5-verb reconciliation scope; the real `mu-engine-server` has
        no `/share` route yet — a separate, pre-existing gap, same as `search()`/`ask()`).

        Shared-plane-gated (`visibility` is one of B0's `SHARED_PLANE_FIELDS`) — a no-op guard
        on the legacy/shared-configured default, but load-bearing the moment a caller constructs a
        private-only (`shared_configured=False`) client via `config=`.

        Returns `MemoryResponse` (design §2.5, already pinned — "already fixed", Decision B: "not
        a new decision") — the full row, consistent with `get()` also returning the full row for
        the same "this is a read of settled state, not a write receipt" reason."""
        validate_plane_fields(
            {"visibility": visibility},
            private_configured=self._private_configured,
            shared_configured=self._shared_configured,
        )
        response = await self._execute(
            "POST",
            f"/v1/memories/{memory_id}/share",
            json_body={"visibility": visibility.value},
        )
        return MemoryResponse.model_validate(response.json_body)

    # ---- lifecycle (TO BUILD, build-queue item 5 — honest 501, never a silent no-op) ----

    async def promote(self, memory_id: str, *, to_tier: str) -> MemoryWriteResult:
        """`POST /v1/memories/{id}/promote` (api-mcp-surface-spec.md §4.3b) is DESIGNED but has NO
        engine-side implementation anywhere in the tree yet: *"no engine method exists yet either
        (today's only promotion is implicit-on-ingest, `add(promote=True)`)... the facade method
        raises `NotImplementedError` (never a fake 200) until the engine counterpart lands"*
        (api-mcp-surface-spec.md §4.3b). This is the wire twin of that exact honesty: raises the
        NAMED `SurfaceVerbNotImplementedError` (`status_code=501`) immediately, with NO network
        call — there is nothing on the other end to call yet. Never a silent no-op or a partial
        success (design §2.5, DEV-STANDARDS rule 8). Return type is annotated `MemoryWriteResult`
        (the receipt shape `SDK-BUILD-DECISIONS.md` Decision B already assigns for when this DOES
        get built — "reuse the `add` receipt shape") purely for future signature stability; this
        method never actually returns.

        `memory_id`/`to_tier` are accepted (matching the documented future request shape,
        `{to_tier: MemoryTier, manager_mode?}`) but unused — there is no request to build yet."""
        raise SurfaceVerbNotImplementedError(
            f"MemoryClient.promote(memory_id={memory_id!r}, to_tier={to_tier!r}) is not "
            "implemented: no engine/wire counterpart exists yet (build-queue item 5). Use "
            "recall()/get() plus a manual add() until this lands.",
            status_code=501,
        )

    async def demote(self, memory_id: str, *, to_tier: str) -> MemoryWriteResult:
        """See `promote()` — the identical honest-`501` twin for the opposite tier transition
        (`POST /v1/memories/{id}/demote`, api-mcp-surface-spec.md §4.3b), same reasoning, same
        NAMED error, same NO-network-call discipline."""
        raise SurfaceVerbNotImplementedError(
            f"MemoryClient.demote(memory_id={memory_id!r}, to_tier={to_tier!r}) is not "
            "implemented: no engine/wire counterpart exists yet (build-queue item 5). Use "
            "recall()/get() plus a manual add() until this lands.",
            status_code=501,
        )

    async def consolidate(
        self,
        *,
        user: str | None = None,
        session: str | None = None,
        limit: int = 50,
    ) -> ConsolidateView:
        """`POST /v1/memories/consolidate` — MTM->LTM DISTILL: extracts bi-temporal SPO facts from
        the recent STM/MTM window and writes them into the LTM graph, applying invalidate-don't-
        delete SUPERSESSION (the MemGC/Phi headline capability).

        **Private-plane path** (`self._private_configured`): canonical `ConsolidateRequest`
        {user,session,limit} — field-for-field identical to the REAL `mu-engine-server`'s own
        schema (`mu_engine_server/schemas.py::ConsolidateRequest`, verified directly). **Shared/
        legacy path**: `{"limit": ...}` only, byte-identical to this method's pre-R2 behavior —
        tenancy resolved server-side from the auth identity, same as before.

        **Return DTO is `ConsolidateView`** (R0's "A4 winner" — was `mu_sdk.models.consolidate.
        ConsolidateResult`, a genuine, documented BREAKING CHANGE to this method's Python return
        type: the real server already returns `ConsolidateView` directly, `noop`-carrying, no
        `generated_at`; see `_parse_consolidate_response`'s own docstring for the two response
        shapes this method now transparently accepts, including `EmbeddedTransport`'s own
        not-yet-updated fabricated payload).

        `user`/`session` are validated via `validate_plane_fields` exactly like every other
        private-plane field on this class."""
        validate_plane_fields(
            {"user": user, "session": session},
            private_configured=self._private_configured,
            shared_configured=self._shared_configured,
        )
        if self._private_configured:
            request = _CanonicalConsolidateRequest(user=user, session=session, limit=limit)
            body = request.model_dump(mode="json", exclude_none=True)
        else:
            legacy_request = _LegacyConsolidateRequest(limit=limit)
            body = legacy_request.model_dump(mode="json", exclude_none=True)
        response = await self._execute("POST", "/v1/memories/consolidate", json_body=body)
        return _parse_consolidate_response(response.json_body)

    async def ask(self, question: str, *, limit: int | None = None) -> AskResult:
        """`POST /v1/memories/ask` (net-new this phase) — MU's own SLM-powered synthesis over
        recalled context (contrast with the raw ranked list `recall()`/`search()` return). Raises
        `ServiceUnavailableError` (mapped from the server's 503) when the server has no LLM/SLM
        configured (heuristic mode) — never a silently-empty answer.

        Untouched by R2 (out of the 5-verb reconciliation scope; the real `mu-engine-server` has
        no `/v1/memories/ask` route yet — a separate, pre-existing gap)."""
        request = AskRequest(
            question=question,
            limit=limit if limit is not None else self._settings.default_recall_limit,
        )
        response = await self._execute(
            "POST",
            "/v1/memories/ask",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )
        return AskResult.model_validate(response.json_body)

    # ---- lifecycle ----

    async def aclose(self) -> None:
        """Idempotent close. Only closes the transport this client constructed itself — an
        injected `Transport` is owned by its caller (repository-pattern discipline)."""
        if self._owns_transport:
            await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
