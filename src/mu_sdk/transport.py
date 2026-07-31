"""The `Transport` port — the seam that makes `httpx` swappable/testable (DEV-STANDARDS rule 5:
"repository pattern for all data access" generalized to the one external dependency this SDK has:
the HTTP client). `MemoryClient` never imports `httpx` directly outside `HttpxTransport`; every
other module talks to the `Transport` protocol.

Port-of-pattern: `mem0.MemoryClient.__init__` builds one `httpx.Client(base_url=..., headers=...,
timeout=...)` and reuses it for every call (`other_repos/mem0/mem0/client/main.py:87-94`). This
module keeps that "one client, reused" shape but behind a protocol so a conformance test can swap
in a deterministic fake transport for pure-unit tests (never for integration tests — those use the
real `HttpxTransport` over real TCP, DEV-STANDARDS "zero mocks").

**Stage D (build-plan §5, task D1) additions — the 3 transport adapters `SdkConfig.mode` selects
between** (design §1.2's table):

- `LocalServerTransport` / `RemoteTransport` — both THIN, behavior-free subclasses of
  `HttpxTransport`. Design §1.2: "transports differ only in serialization + auth, never behavior"
  — for these two the serialization is IDENTICAL too (both are plain JSON-over-HTTP against the
  same route family), so the only real difference between them is which `SdkSettings.base_url`/
  auth `MemoryClient.__init__` built them with. The two classes exist so `mode` selection has a
  real adapter object per design's transport-adapter table (§1.2) and so a 4th adapter
  (`GrpcTransport`, deferred per §5) can be added later with zero change to the other two — not
  because either overrides any `HttpxTransport` behavior.
- `EmbeddedTransport` — the in-process, no-wire adapter (`mode="embedded"`, Python-only). Routes
  the SAME HTTP-shaped `(method, path, json_body, params)` calls the frozen `MemoryClient` verb
  bodies already issue (B2, `client.py` — not edited by this task) onto a lazily-constructed
  `mu_local.local_memory.LocalMemory` instance, then re-serializes LocalMemory's return value into
  the exact wire JSON shape each verb body's own `.model_validate(...)` call expects — the same
  "translate to the wire shape" job `HttpxTransport` performs by making a real HTTP round-trip,
  done here in-process instead (no sockets, no serialization over a wire — design §5: "`embedded`
  mode has no wire at all"). See `EmbeddedTransport`'s own docstring for the full route table, the
  LAZY `mu_local` import (the `[embedded]` optional-extra boundary, PACKAGING-v2 §5.1/§1.3), and
  the documented, per-route translation gaps this honestly does NOT paper over.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from mu_sdk.errors import SdkError, SdkTimeoutError, TransportError
from mu_sdk.settings import SdkSettings

if TYPE_CHECKING:  # pragma: no cover — static-analysis only; see EmbeddedTransport's own docstring
    # for why the REAL import must stay lazy (inside __init__) at runtime.
    from mu_sdk.config import SdkConfig

__all__ = [
    "DEFAULT_ENGINE_SERVER_TOKEN_PATH",
    "ENGINE_SERVER_TOKEN_PATH_ENV_VAR",
    "EmbeddedExtraNotInstalledError",
    "EmbeddedTransport",
    "EngineServerTokenMissingError",
    "HttpxTransport",
    "LocalServerTransport",
    "NullAuth",
    "RemoteTransport",
    "Transport",
    "TransportResponse",
    "UnroutedEmbeddedCallError",
    "load_engine_server_token",
]


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """The transport-agnostic response envelope every `Transport` implementation returns."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    json_body: Any | None = None
    text: str = ""

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup (HTTP header names are case-insensitive)."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


class Transport(Protocol):
    """The port. `HttpxTransport` is the shipped adapter; a test may bind a fake for pure-unit
    coverage of error-mapping/retry logic without a real socket."""

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> TransportResponse: ...

    async def aclose(self) -> None: ...


class HttpxTransport:
    """The shipped `Transport` adapter — one `httpx.AsyncClient`, fully async, cancellation-safe
    (DEV-STANDARDS rule 1: `asyncio.CancelledError` is never caught here; `httpx` itself is
    cancellation-safe on `await` boundaries and this method adds no `except Exception` around the
    await that could swallow it)."""

    def __init__(self, settings: SdkSettings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(
                settings.timeout_s,
                connect=settings.connect_timeout_s,
            ),
            headers={"User-Agent": settings.user_agent},
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> TransportResponse:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout_s if timeout_s is not None else self._settings.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise SdkTimeoutError(f"request timed out: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise TransportError(f"transport failure: {method} {path}: {exc}") from exc

        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None

        return TransportResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            json_body=body,
            text=response.text,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# ==================================================================================================
# Stage D (build-plan §5 D1) — the 3 mode-selected transport adapters + token auto-load (design
# §1.2 FIX 4). See module docstring for the design-level summary.
# ==================================================================================================


class LocalServerTransport(HttpxTransport):
    """``mode="local_server"`` adapter — HTTP to a local ``mu-engine-server`` container over
    ``http://localhost:<port>`` (design §1.2). Adds no behavior over `HttpxTransport` (module
    docstring) — `MemoryClient.__init__` constructs this with an `SdkSettings.base_url` set to
    `SdkConfig.endpoint` and an auto-loaded bearer token when no `auth=` was given (FIX 4, see
    `load_engine_server_token` below)."""


class RemoteTransport(HttpxTransport):
    """``mode="remote"`` adapter — HTTP to the hosted, governed, multi-tenant ``mu-server`` plane
    (design §1.2). Adds no behavior over `HttpxTransport` (module docstring) — identical wire
    protocol to `LocalServerTransport`, differing only in the `endpoint`/`auth`
    `MemoryClient.__init__` resolves it with (design §1.3: "differ only in `endpoint` + `auth`")."""


class NullAuth:
    """The `embedded` mode's `SdkAuth`-duck-typed no-op (design §1.2: "auth is N/A (in-process)").
    `MemoryClient._raw_request` unconditionally calls `self._auth.headers()` to build every
    request's headers regardless of transport (`client.py`, not edited by this task) — for
    `EmbeddedTransport` those headers are harmlessly discarded (there is no wire to put them on),
    so this returns an empty mapping rather than requiring a real credential nothing will read."""

    def headers(self) -> dict[str, str]:
        return {}


# ---- token auto-load (design §1.2 FIX 4 / §2.4/§11.2, build-plan §13 item 7) --------------------
# PORT of `mu_engine_server.auth`'s own `DEFAULT_TOKEN_PATH`/`TOKEN_PATH_ENV_VAR`/`load_token`
# (`mu-core/packages/mu-engine-server/src/mu_engine_server/auth.py:65-102` — read directly before
# writing this, not guessed) — that module lives in a SEPARATE repo/package this one may never
# import (`mu_engine_server` is not a `mu-sdk-python` dependency, and importing it would pull in
# `mu_engine`/FastAPI transitively, exactly the boundary this file's own module docstring exists to
# avoid). Deliberately the SAME default path + the SAME env-var NAME so a token minted by
# `mu-engine-server`'s `make up` (Stage E) is discoverable by this SDK with zero extra config —
# a caller who only sets `MU_ENGINE_SERVER_TOKEN_PATH` for the server side gets it honored here too.
DEFAULT_ENGINE_SERVER_TOKEN_PATH: Path = Path.home() / ".memory-universe" / "engine-server.token"
ENGINE_SERVER_TOKEN_PATH_ENV_VAR = "MU_ENGINE_SERVER_TOKEN_PATH"  # noqa: S105 - env var NAME


class EngineServerTokenMissingError(SdkError):
    """The NAMED error `mode="local_server"` with no `auth=` raises when the auto-loaded token
    file is absent, unreadable, or empty (design §1.2 FIX 4: "the SDK AUTO-LOADS it... Pass
    `auth=BearerAuth(...)` explicitly to override") — never a bare `FileNotFoundError`/
    `OSError` (DEV-STANDARDS rule 8: fail-loud with a NAMED type, not a generic exception a caller
    has to `except Exception` to catch)."""


def load_engine_server_token(token_path: str | Path | None = None) -> str:
    """Resolve (arg > `MU_ENGINE_SERVER_TOKEN_PATH` env > `~/.memory-universe/engine-server.token`)
    and read the per-process bearer token `mu-engine-server`'s `make up` mints to disk (Stage E).
    Raises `EngineServerTokenMissingError` — never a bare exception — on a missing/unreadable/blank
    file; the resolved path is included in the message (a local filesystem path is not a secret)."""
    if token_path is not None:
        path = Path(token_path)
    else:
        env_override = os.environ.get(ENGINE_SERVER_TOKEN_PATH_ENV_VAR)
        path = Path(env_override) if env_override else DEFAULT_ENGINE_SERVER_TOKEN_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EngineServerTokenMissingError(
            f"mode='local_server' has no auth= and no engine-server token found at {path} — "
            "run `make up` (Stage E) to mint one, or pass auth=BearerAuth(...) explicitly."
        ) from exc
    except OSError as exc:
        raise EngineServerTokenMissingError(
            f"mode='local_server' engine-server token file unreadable: {path}"
        ) from exc
    token = raw.strip()
    if not token:
        raise EngineServerTokenMissingError(
            f"mode='local_server' engine-server token file is empty: {path}"
        )
    return token


# ---- EmbeddedTransport (mode="embedded", Python-only) --------------------------------------------


class EmbeddedExtraNotInstalledError(SdkError):
    """`mode="embedded"` was requested but `mu-local` (the `[embedded]` optional extra,
    PACKAGING-v2 §5.1/§1.3) is not importable — raised at `EmbeddedTransport.__init__`, from the
    ORIGINAL `ImportError` (never swallowed), so the fix (`pip install 'mu-sdk[embedded]'`) is
    immediately legible instead of a bare `ModuleNotFoundError` deep in a lazy import."""


class UnroutedEmbeddedCallError(SdkError):
    """A verb body issued an HTTP-shaped call (`method`, `path`) `EmbeddedTransport`'s route table
    (below) has no handler for. This is an HONEST gap, not a silent no-op (DEV-STANDARDS rule 8):
    some frozen `MemoryClient` verb bodies (`get`/`share`/`search`/`ask`/`.context.discover`) were
    written against paths/response shapes B2's conformance fake server serves, which do not
    line up 1:1 with either `LocalMemory`'s own verb surface or (verified separately, real
    subprocess test, see this task's report) the REAL `mu-engine-server`'s route/schema set either
    — a genuine, pre-existing B2/C interop gap this task does not own and cannot fix without
    editing frozen verb bodies. `add`/`recall`/`consolidate`/`build_context` — the verbs this
    task's own REAL-TEST GATE requires — ARE routed (see `EmbeddedTransport`'s docstring)."""


class EmbeddedTransport:
    """``mode="embedded"`` adapter (design §1.2, Python-only) — no wire at all. Lazily builds a
    real `mu_local.local_memory.LocalMemory` over real stores (`SdkConfig.storage`/`workspace`/
    `namespace`/`lifecycle`) and translates the SAME HTTP-shaped `(method, path, json_body,
    params)` calls `MemoryClient`'s frozen verb bodies already issue into real `LocalMemory` calls,
    then re-serializes the result into the exact wire JSON shape each verb body's own
    `.model_validate(...)` parses — so the verb bodies (not edited by this task) behave
    byte-identically regardless of which transport backs them, for every route this class routes.

    **LAZY import (the optional-extra boundary).** `mu_local` (and transitively `mu_engine`) is
    imported HERE, inside `__init__`, never at module scope — `mu_sdk.transport` (this module) is
    imported unconditionally by `mu_sdk/__init__.py`, so a top-level `import mu_local` here would
    make the DEFAULT install (no `[embedded]` extra) require `mu-local`, breaking the
    `sdk-has-no-engine` `.importlinter` contract for every caller, not just `mode="embedded"` ones.
    A caller who never constructs `mode="embedded"` never triggers this import at all; a caller who
    does and has not installed `mu-sdk[embedded]` gets `EmbeddedExtraNotInstalledError` (named,
    from the original `ImportError`), not a bare `ModuleNotFoundError`.

    **Route table + the documented translation gaps** (verified against `LocalMemory`'s real
    signature, `mu-core/packages/mu-local/src/mu_local/local_memory.py`, read directly, not
    guessed):

    - ``POST /memories`` (`add`) -> `LocalMemory.add(content)`. The wire request's
      `visibility`/`subject`/`predicate`/`object` fields (client.py's `MemoryCreateRequest`
      defaults `visibility=SHARED`) are DELIBERATELY DROPPED, never forwarded — `LocalMemory` is
      private-plane-only by construction and its own `add()` REJECTS any non-`None` value for
      those fields (`PlaneFieldRejectedError`); embedded mode has no shared plane for `visibility`
      to select between in the first place, so there is nothing honest to forward. `add()`'s
      verb body then parses the response as a full `MemoryResponse` (37 fields, Appendix A.1) —
      NOT the `MemoryWriteResult` receipt `LocalMemory.add()` (and the real `mu-engine-server`'s
      own `POST /memories` route, verified separately) actually return — so this handler
      fabricates the extra fields (`content_type`/`state`/`access_count`/`created_at`/
      `updated_at`) a fresh write always has (same precedent B2's own conformance fake server
      already sets, `tests/conformance_server/app.py::upsert_memory` — this is not a new invention,
      it re-derives the identical shape from the identical receipt fields).
    - ``POST /v1/memories/recall`` (`recall`) -> `LocalMemory.recall(text, limit=..., tier=...)`.
      `channels`/`mode`/`persona`/`max_tokens`/`correlation_id` have no `LocalMemory` equivalent
      and are dropped (documented gap, not silently mis-mapped). The return IS the canonical
      `mu_contracts.contracts.recall.RecallResult` — the identical class `mu_sdk.models.recall.
      RecallResult` re-exports (verified: `RecallResult.model_dump(mode="json")` round-trips
      through the verb body's own `RecallResult.model_validate(...)` with zero re-shaping needed).
    - ``POST /v1/memories/consolidate`` (`consolidate`) -> `LocalMemory.consolidate(limit=...)`.
      Returns `mu_contracts.contracts.views.ConsolidateView` (`facts_extracted/added/superseded/
      noop`); the verb body parses `mu_sdk.models.consolidate.ConsolidateResult`, a SEPARATE class
      with `generated_at` instead of `noop` (`extra="forbid"` on both sides) — this handler maps
      the 3 shared fields across and fabricates `generated_at=now()` (the sweep's own real
      completion time; `noop` has no destination field and is dropped, not silently reinterpreted
      as one of the other three).
    - ``POST /v1/context/window`` (`build_context`) -> `LocalMemory.context(text, limit=...,
      max_chars=...)`. Returns the canonical `mu_contracts.contracts.views.ContextView` — the
      IDENTICAL class `mu_sdk.models.context.ContextView` re-exports (zero re-shaping, same as
      `recall` above).
    - Every other route (`GET /memories` search, `GET .../{id}` get, `.../share`,
      `/v1/memories/ask`, `/v1/context/discover`) raises `UnroutedEmbeddedCallError` — see that
      class's own docstring for why (a genuine, pre-existing B2/C interop gap, not this task's to
      silently paper over with fabricated data)."""

    def __init__(self, config: SdkConfig) -> None:
        try:
            from mu_local.local_memory import LocalMemory
        except ImportError as exc:
            raise EmbeddedExtraNotInstalledError(
                "SdkConfig(mode='embedded') requires the optional 'embedded' extra: "
                "install with `pip install 'mu-sdk[embedded]'` (or the repo's `uv pip install "
                "-e '.[embedded]'` equivalent) to make `mu-local` importable."
            ) from exc

        self._memory = LocalMemory(
            config.storage,
            workspace=config.workspace,
            namespace=config.namespace,
            lifecycle=config.lifecycle,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> TransportResponse:
        del headers, timeout_s  # no wire — nothing to attach headers to, no network timeout to set
        body = json_body or {}
        query = params or {}
        if method == "POST" and path == "/memories":
            return await self._add(body)
        if method == "POST" and path == "/v1/memories/recall":
            return await self._recall(body, query)
        if method == "POST" and path == "/v1/memories/consolidate":
            return await self._consolidate(body)
        if method == "POST" and path == "/v1/context/window":
            return await self._build_context(body)
        raise UnroutedEmbeddedCallError(
            f"EmbeddedTransport has no route for {method} {path} (see EmbeddedTransport's own "
            "docstring for the full route table and the documented, honest gaps)."
        )

    async def _add(self, body: dict[str, Any]) -> TransportResponse:
        content = body["content"]
        # visibility/subject/predicate/object are deliberately NOT forwarded — see this class's
        # own docstring's `POST /memories` route entry for why.
        receipt = await self._memory.add(content)
        now = _now_iso()
        tier = receipt.tiers_written[-1] if receipt.tiers_written else "stm"
        payload = {
            "id": receipt.memory_id,
            "content": content if isinstance(content, str) else str(content),
            "content_type": "text",
            "tier": tier,
            "state": "active",
            "importance_score": body.get("importance_score", 0.5),
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
            "namespace": receipt.namespace,
            "metadata": body.get("metadata", {}),
            "subject": None,
            "predicate": None,
            "object": None,
            "parent_ids": [],
            "child_ids": [],
            "content_hash": receipt.content_hash,
        }
        return TransportResponse(status_code=201, json_body=payload)

    async def _recall(self, body: dict[str, Any], query: dict[str, Any]) -> TransportResponse:
        from mu_engine.storage.domain.memory import MemoryTier

        tier_param = query.get("tier")
        tier = MemoryTier(tier_param) if tier_param else None
        result = await self._memory.recall(
            body["text"],
            limit=body.get("limit", 10),
            tier=tier,
        )
        return TransportResponse(status_code=200, json_body=result.model_dump(mode="json"))

    async def _consolidate(self, body: dict[str, Any]) -> TransportResponse:
        result = await self._memory.consolidate(limit=body.get("limit", 50))
        payload = {
            "facts_extracted": result.facts_extracted,
            "added": result.added,
            "superseded": result.superseded,
            "generated_at": _now_iso(),
        }
        return TransportResponse(status_code=200, json_body=payload)

    async def _build_context(self, body: dict[str, Any]) -> TransportResponse:
        result = await self._memory.context(
            body["text"],
            limit=body.get("limit", 10),
            max_chars=body.get("max_chars"),
        )
        return TransportResponse(status_code=200, json_body=result.model_dump(mode="json"))

    async def aclose(self) -> None:
        await self._memory.aclose()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
