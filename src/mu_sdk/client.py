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

**Plane-gating (design §2.5 "Unified verb surface"; build-plan Stage B ruling 1).** `add`/`recall`/
`build_context`/`share` accept the canonical superset signature's plane-gated kwargs
(`user`/`session`/`agent` — private-plane; `visibility`/`subject`/`predicate`/`object` — shared-
plane), validated by `mu_contracts.validation.plane_gate.validate_plane_fields` — the SAME
validator `LocalMemory` (mu-local, B1) calls, so a field supplied for a plane this class has not
configured is REJECTED (`PlaneFieldRejectedError`), never silently dropped or accepted-and-ignored.
`MemoryClient` today has NO `SdkConfig.mode` toggle (Stage D, design §3) to select a private plane
— it is unconditionally the SHARED-plane wire client, so every private-plane field is honestly
rejected until Stage D wires a real toggle through (see the module-level `_PRIVATE_PLANE_CONFIGURED`
constant below).

Cancellation (DEV-STANDARDS rule 1): every await here is a direct `await` with no surrounding
`except Exception`/`except BaseException` — `asyncio.CancelledError` propagates untouched through
this class, through the decorator stack (see `decorators.py` docstring), and through
`HttpxTransport` (a bare `httpx` await). `aclose()` is idempotent and safe to call from a
`finally` block on cancellation.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from mu_contracts.domain.model.memory import Visibility
from mu_contracts.validation.plane_gate import validate_plane_fields

from mu_sdk.auth import BearerAuth, SdkAuth, resolve_auth
from mu_sdk.config import SdkConfig
from mu_sdk.decorators import with_retry, with_timeout, with_trace
from mu_sdk.error_mapping import raise_for_wire_error
from mu_sdk.errors import AuthenticationError, NotFoundError, SurfaceVerbNotImplementedError
from mu_sdk.models.consolidate import AskRequest, AskResult, ConsolidateRequest, ConsolidateResult
from mu_sdk.models.context import ContextIndexListView, ContextView
from mu_sdk.models.memory import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
    MemoryWriteResult,
)
from mu_sdk.models.recall import RecallChannels, RecallMode, RecallRequest, RecallResult
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

# Plane configuration (interim, pre-Stage-D — design §3/§4's `SdkConfig.mode` selector does not
# exist on this class yet). `MemoryClient` today is UNCONDITIONALLY the SHARED-plane wire client:
# it has no "embedded"/"local_server" mode toggle to gain a private plane. Every private-plane
# field (`user`/`session`/`agent`) is therefore honestly REJECTED (design §2.5: "a rejection, not
# a silent no-op"), never silently accepted-and-ignored, until Stage D wires a real toggle through
# `SdkConfig`. Module-level (not per-instance) because no constructor arg controls this yet either.
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

    `config.shared` (dual-plane, design §4) is deliberately NOT consulted here — see `SdkConfig.
    shared`'s own docstring in `mu_sdk.config` for why per-call private/shared dispatch is out of
    D1's scope (it needs the frozen verb bodies' module-level `_PRIVATE_PLANE_CONFIGURED`/
    `_SHARED_PLANE_CONFIGURED` constants above to become real per-instance state, which this task
    does not touch)."""
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
    "byte-identical code, config picks the transport" guarantee (design §1/§6). Every verb body
    below (B2, frozen — this task edits ONLY this constructor / the module-level plane-gating
    note above) then behaves identically regardless of which transport it landed on, module
    caveats documented on `EmbeddedTransport`/`load_engine_server_token` in `mu_sdk.transport`.

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
        visibility: Visibility = Visibility.SHARED,
        tier: str = "stm",
        importance_score: float = 0.5,
        idempotency_key: str | None = None,
        local_memory_id: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,  # matches the frozen wire field name (Appendix A.1) exactly
        metadata: dict[str, str] | None = None,
        user: str | None = None,
        session: str | None = None,
        agent: str | None = None,
    ) -> MemoryWriteResult:
        """`POST /memories` (api-mcp-surface-spec.md §4.3; Appendix A.1). Shared `POST /memories`
        rejects `visibility=PRIVATE` server-side (`app.py:1690`) — the SDK has no
        private-to-shared leak path; a PRIVATE write raises `PrivateDataRejectedError`
        (`mu_sdk.error_mapping`), it is never silently coerced to SHARED.

        `idempotency_key`, when given, is sent as the `Idempotency-Key` HEADER (api-mcp-
        surface-spec.md §2.3 write-idempotency contract) — never duplicated into the JSON body.

        **Return DTO is `MemoryWriteResult`** (design §2.5 "Return DTOs", `SDK-BUILD-DECISIONS.md`
        Decision B) — a write RECEIPT, not the full row (`get()`/`share()` return
        `MemoryResponse` for that). The wire's `POST /memories` HTTP body is UNCHANGED (still the
        frozen `MemoryResponse` shape, Appendix A.1, CANONICAL-CONTRACTS.md untouched); this
        method maps that response DOWN to the receipt: `memory_id<-id`, `content_hash<-
        content_hash`, `namespace<-namespace`. `promoted`/`tiers_written` are NOT on the wire
        body, so they are approximated client-side (`promoted <- tier != "stm"`, `tiers_written <-
        (tier,)`, the terminal tier written) — a documented approximation (Decision B's
        "Wire-transport note"), never a silent invention. `events_emitted` stays the receipt's
        default `()` (the wire body carries no event list either).

        `user`/`session`/`agent` (design §2.5's private-plane fields) are accepted on this
        signature for surface parity with `LocalMemory.add` but are validated via
        `validate_plane_fields` and ALWAYS REJECTED today (see the module-level
        `_PRIVATE_PLANE_CONFIGURED` note) — this class has no private plane until Stage D.
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
            private_configured=_PRIVATE_PLANE_CONFIGURED,
            shared_configured=_SHARED_PLANE_CONFIGURED,
        )
        request = MemoryCreateRequest(
            content=content,
            visibility=visibility,
            tier=tier,  # type: ignore[arg-type]  # validated by MemoryCreateRequest's Literal
            importance_score=importance_score,
            local_memory_id=local_memory_id,
            subject=subject,
            predicate=predicate,
            object=object,
            metadata=metadata or {},
        )
        headers = {_IDEMPOTENCY_KEY_HEADER: idempotency_key} if idempotency_key else None
        response = await self._execute(
            "POST",
            "/memories",
            json_body=request.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )
        wire_response = MemoryResponse.model_validate(response.json_body)
        return MemoryWriteResult(
            memory_id=wire_response.id,
            content_hash=wire_response.content_hash,
            promoted=wire_response.tier != "stm",
            tiers_written=(wire_response.tier,),
            namespace=wire_response.namespace,
        )

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
        row). For channel/mode/persona control use `.recall()` instead."""
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
        identity, never sent by the client (see that same module docstring).

        `tier` (net-new this phase: `"stm"|"mtm"|"ltm"`), when given, is sent as the `?tier=`
        QUERY param (not a body field) and always wins server-side over `channels` — tier-SCOPED
        recall narrowed to exactly one real channel (the demo server's `_effective_tier_filter`).
        `None` (the default) leaves channel selection to `channels`/`mode` as before — no
        behaviour change for an existing caller that doesn't pass `tier`.

        `user`/`session`/`agent` (design §2.5's private-plane fields, net-new this phase) are
        accepted on this signature for surface parity with `LocalMemory.recall` but are validated
        via `validate_plane_fields` and ALWAYS REJECTED today — see `add()`'s docstring / the
        module-level `_PRIVATE_PLANE_CONFIGURED` note for why."""
        validate_plane_fields(
            {"user": user, "session": session, "agent": agent},
            private_configured=_PRIVATE_PLANE_CONFIGURED,
            shared_configured=_SHARED_PLANE_CONFIGURED,
        )
        request = RecallRequest(
            text=text,
            limit=limit if limit is not None else self._settings.default_recall_limit,
            channels=channels or RecallChannels(),
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
            json_body=request.model_dump(mode="json", exclude_none=True),
        )
        return RecallResult.model_validate(response.json_body)

    async def get(self, memory_id: str) -> MemoryResponse | None:
        """`GET /v1/memories/{memory_id}` (net-new this phase, design §2.5: "`get` — kept, exists
        only on `LocalMemory` today — TO BUILD on the wire/`MemoryClient` side so both transports
        expose it"). No frozen route is pinned for a single-memory GET in Appendix A.1, so this
        targets the placeholder path this phase's conformance server also implements (see
        `tests/conformance_server/app.py`), matching the established `/v1`-prefixed convention for
        every other net-new route in this class.

        Returns `None` on a 404 (not-found) rather than raising `NotFoundError` — a point-get miss
        is a perfectly normal outcome, mirroring `LocalMemory.get`'s `| None` miss signal
        (`SDK-BUILD-DECISIONS.md` Decision B: "keep the `| None` miss signal that embedded `get`
        already returns")."""
        try:
            response = await self._execute("GET", f"/v1/memories/{memory_id}")
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
        """`POST /v1/context/window` (design §2.5 REVIEW-2 FIX 1; §2 of
        `2026-07-31-sdk-engine-server-design.md`'s route inventory pins this exact placeholder
        path) — `LocalMemory.context(...)`'s WIRE TWIN: the PRIVATE-plane context-*WINDOW* helper
        (deterministic recall + render, NO LLM synthesis, `mu_local.views.ContextView`'s
        `SDK-BUILD-DECISIONS.md` Decision B rationale).

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
        `user`/`session`/`agent` (module-level `_PRIVATE_PLANE_CONFIGURED` note): always rejected
        today, since this class has no private plane until Stage D wires one through. They are
        never sent as wire body fields (tenancy is header/auth-derived, same rule as every other
        verb) — a caller that supplies either is rejected before any request is built."""
        validate_plane_fields(
            {"user": user, "session": session},
            private_configured=_PRIVATE_PLANE_CONFIGURED,
            shared_configured=_SHARED_PLANE_CONFIGURED,
        )
        request_body: dict[str, Any] = {
            "text": text,
            "limit": limit if limit is not None else self._settings.default_recall_limit,
        }
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

        Shared-plane-gated (`visibility` is one of B0's `SHARED_PLANE_FIELDS`) — a no-op guard
        today since this class is unconditionally shared-plane-configured
        (`_SHARED_PLANE_CONFIGURED`, module-level note), but becomes load-bearing the moment
        Stage D lets a caller construct a `shared=None` (shared-plane-NOT-configured) client.

        Returns `MemoryResponse` (design §2.5, already pinned — "already fixed", Decision B: "not
        a new decision") — the full row, consistent with `get()` also returning the full row for
        the same "this is a read of settled state, not a write receipt" reason."""
        validate_plane_fields(
            {"visibility": visibility},
            private_configured=_PRIVATE_PLANE_CONFIGURED,
            shared_configured=_SHARED_PLANE_CONFIGURED,
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

    async def consolidate(self, *, limit: int = 50) -> ConsolidateResult:
        """`POST /v1/memories/consolidate` (net-new this phase) — MTM->LTM DISTILL: extracts
        bi-temporal SPO facts from the recent STM/MTM window and writes them into the LTM graph,
        applying invalidate-don't-delete SUPERSESSION (the MemGC/Phi headline capability).
        Tenancy is resolved server-side from the auth identity, same as every other verb."""
        request = ConsolidateRequest(limit=limit)
        response = await self._execute(
            "POST",
            "/v1/memories/consolidate",
            json_body=request.model_dump(mode="json", exclude_none=True),
        )
        return ConsolidateResult.model_validate(response.json_body)

    async def ask(self, question: str, *, limit: int | None = None) -> AskResult:
        """`POST /v1/memories/ask` (net-new this phase) — MU's own SLM-powered synthesis over
        recalled context (contrast with the raw ranked list `recall()`/`search()` return). Raises
        `ServiceUnavailableError` (mapped from the server's 503) when the server has no LLM/SLM
        configured (heuristic mode) — never a silently-empty answer."""
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
