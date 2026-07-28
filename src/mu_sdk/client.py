"""`MemoryClient` — the async wire client (mu-local-and-sdk-spec.md §2.3; port-of-pattern
`mem0.MemoryClient`, `other_repos/mem0/mem0/client/main.py:24-105`).

Exposes `add` (write), `search` (simple ranked list, mem0 muscle-memory), `recall` (the
MU-canonical rich multi-channel read — now with an optional `tier=` arg for tier-SCOPED recall,
net-new this phase), `consolidate` (MTM->LTM DISTILL: invalidate-don't-delete SUPERSESSION +
bi-temporal SPO extraction, net-new this phase), `ask` (MU's own SLM-powered synthesis over
recalled context, net-new this phase), and `.context` (the `ContextApi` sub-client's read-only
`discover`). No engine algorithm, no store adapter — every verb is one HTTP call through the
`Transport` port, wrapped by the retry/timeout/trace decorator stack (`mu_sdk.decorators`), with a
typed error raised via `mu_sdk.error_mapping` on any non-2xx response.

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

from mu_sdk.auth import SdkAuth, resolve_auth
from mu_sdk.decorators import with_retry, with_timeout, with_trace
from mu_sdk.error_mapping import raise_for_wire_error
from mu_sdk.models.consolidate import AskRequest, AskResult, ConsolidateRequest, ConsolidateResult
from mu_sdk.models.context import ContextIndexListView
from mu_sdk.models.memory import MemoryCreateRequest, MemoryListResponse, MemoryResponse
from mu_sdk.models.recall import RecallChannels, RecallMode, RecallRequest, RecallResult
from mu_sdk.settings import SdkSettings
from mu_sdk.transport import HttpxTransport, Transport, TransportResponse

__all__ = ["ContextApi", "MemoryClient"]

_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"  # api-mcp-surface-spec.md §2.3


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
    error-mapping/retry logic — never mocked in integration tests)."""

    def __init__(
        self,
        *,
        settings: SdkSettings | None = None,
        transport: Transport | None = None,
        auth: SdkAuth | None = None,
    ) -> None:
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
    ) -> MemoryResponse:
        """`POST /memories` (api-mcp-surface-spec.md §4.3; Appendix A.1). Shared `POST /memories`
        rejects `visibility=PRIVATE` server-side (`app.py:1690`) — the SDK has no
        private-to-shared leak path; a PRIVATE write raises `PrivateDataRejectedError`
        (`mu_sdk.error_mapping`), it is never silently coerced to SHARED.

        `idempotency_key`, when given, is sent as the `Idempotency-Key` HEADER (api-mcp-
        surface-spec.md §2.3 write-idempotency contract) — never duplicated into the JSON body.
        """
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
        return MemoryResponse.model_validate(response.json_body)

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
    ) -> RecallResult:
        """`POST /v1/memories/recall` — the MU-canonical rich multi-channel read
        (recall-service-design.md §1.1; see `mu_sdk.models.recall` module docstring for the
        wire-route rationale). Tenancy (`namespace`) is resolved server-side from the auth
        identity, never sent by the client (see that same module docstring).

        `tier` (net-new this phase: `"stm"|"mtm"|"ltm"`), when given, is sent as the `?tier=`
        QUERY param (not a body field) and always wins server-side over `channels` — tier-SCOPED
        recall narrowed to exactly one real channel (the demo server's `_effective_tier_filter`).
        `None` (the default) leaves channel selection to `channels`/`mode` as before — no
        behaviour change for an existing caller that doesn't pass `tier`."""
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
