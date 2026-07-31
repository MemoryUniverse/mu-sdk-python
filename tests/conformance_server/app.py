"""A REAL minimal conformance server — a FastAPI app implementing the add/search/recall/get/
build_context/share/context wire endpoints this SDK phase targets, run under a REAL uvicorn
instance (see `tests/integration/conftest.py`). This is NOT `mu-server` (which does not exist yet);
it is the dev-standards-mandated "real local conformance server" the integration suite's real
`httpx` client makes real TCP requests against — no mock ever stands in for it or for `httpx`.

`GET /v1/memories/{id}` (`.get()`), `POST /v1/context/window` (`.build_context()`), and
`POST /v1/memories/{id}/share` (`.share()`) are net-new this phase (B2, design §2.5) — placeholder
routes (no frozen wire contract pins them yet, same "net-new" status `POST /v1/memories/consolidate`
and `POST /v1/memories/ask` already had before this phase), added here the same way those two were:
the simplest possible REAL implementation of the documented behavior, so B2's integration tests
exercise the real HTTP layer end-to-end (DEV-STANDARDS "zero mocks"), not just request construction.
`promote`/`demote` have NO route here — `MemoryClient.promote()`/`.demote()` never make a network
call at all (see `client.py`'s docstrings), so there is nothing for this server to serve.

Auth: supports both the demo identity headers (`X-Demo-*`, api-mcp-surface-spec.md §2.2) and a
bearer/API-key `Authorization` header — mirroring `DemoHeaderAuth`/`ApiKeyAuth` one-to-one. Missing
both -> 401. Every response, success or error, carries `X-Request-ID` (echoing the caller's trace
header, or minting one) so the SDK's error-mapping `request_id` field has something real to parse.

State is in-process, per-server-instance, in-memory dicts — intentionally the simplest possible
REAL implementation of the documented behaviors (idempotent replay/conflict, visibility rejection,
tenancy scoping by identity), not a production store.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="mu-sdk-python conformance server")


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Flattens `HTTPException(detail={...})` to the TOP-LEVEL frozen REST envelope
    (api-mcp-surface-spec.md §2.4: `{"detail": str, "request_id": str}` [+ additive fields, e.g.
    `code` for the private-data-rejected case]) — FastAPI's default handler would nest our dict
    one level under its own `"detail"` key, which is not what this SDK's `error_mapping` (or the
    real frozen contract) expects."""
    del request
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail), "request_id": str(uuid.uuid4())},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc.errors()), "request_id": str(uuid.uuid4())},
    )


# ---- in-memory state -------------------------------------------------------------------------

_memories: dict[str, dict[str, dict[str, Any]]] = {}  # tenant_key -> {memory_id: MemoryResponse}
_idempotency: dict[
    str, dict[str, tuple[str, dict[str, Any]]]
] = {}  # tenant_key -> {key: (hash, response)}
# `consolidate`/`ask` fake state — per-tenant ACTIVE fact table keyed by (subject, predicate), the
# simplest possible REAL implementation of invalidate-don't-delete SUPERSESSION (module docstring
# philosophy): a later `add` sharing (subject, predicate) with a DIFFERENT object supersedes the
# prior one. `_consolidated_ids` tracks which stored memories a consolidate sweep already consumed
# (so a repeat sweep never double-counts `facts_extracted`).
_facts: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}  # tenant_key -> {(s,p): {...}}
_consolidated_ids: dict[str, set[str]] = {}  # tenant_key -> {memory_id, ...} already swept


class Identity(BaseModel):
    tenant_key: str
    user_id: str
    workspace_id: str
    namespace_id: str
    session_id: str


def _request_id(x_request_id: str | None) -> str:
    return x_request_id or str(uuid.uuid4())


async def resolve_identity(
    x_demo_user_id: Annotated[str | None, Header()] = None,
    x_demo_workspace_id: Annotated[str | None, Header()] = None,
    x_demo_namespace_id: Annotated[str | None, Header()] = None,
    x_demo_session_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_request_id: Annotated[str | None, Header()] = None,
) -> Identity:
    request_id = _request_id(x_request_id)
    if authorization is not None:
        if not authorization.startswith("Bearer ") or len(authorization) <= len("Bearer "):
            raise HTTPException(
                status_code=401,
                detail={"detail": "invalid bearer credential", "request_id": request_id},
            )
        token = authorization.removeprefix("Bearer ")
        if token in ("bad", "revoked", ""):
            raise HTTPException(
                status_code=401,
                detail={"detail": "invalid or revoked API key", "request_id": request_id},
            )
        return Identity(
            tenant_key=f"apikey:{token}",
            user_id="api-key-user",
            workspace_id="api-key-workspace",
            namespace_id="default",
            session_id="api-key-session",
        )
    if x_demo_user_id and x_demo_workspace_id and x_demo_namespace_id and x_demo_session_id:
        tenant_key = f"demo:{x_demo_workspace_id}:{x_demo_namespace_id}:{x_demo_user_id}"
        return Identity(
            tenant_key=tenant_key,
            user_id=x_demo_user_id,
            workspace_id=x_demo_workspace_id,
            namespace_id=x_demo_namespace_id,
            session_id=x_demo_session_id,
        )
    raise HTTPException(
        status_code=401,
        detail={"detail": "no credentials presented", "request_id": request_id},
    )


def _namespace_prefix(identity: Identity) -> str:
    return f"mu/{identity.workspace_id}/{identity.namespace_id}/shared/*/{identity.session_id}"


def _content_hash(body: dict[str, Any]) -> str:
    payload = repr(sorted(body.items())).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---- POST /memories (add) --------------------------------------------------------------------


@app.post("/memories", status_code=201)
async def upsert_memory(
    request: Request,
    response: Response,
    identity: Annotated[Identity, Depends(resolve_identity)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    request_id = _request_id(x_request_id)
    response.headers["X-Request-ID"] = request_id
    body: dict[str, Any] = await request.json()

    if body.get("visibility") == "private":
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "shared POST /memories rejects visibility=private",
                "request_id": request_id,
                "code": "private_data_rejected",
            },
        )

    tenant_store = _memories.setdefault(identity.tenant_key, {})
    idem_store = _idempotency.setdefault(identity.tenant_key, {})

    body_hash = _content_hash(body)
    if idempotency_key is not None:
        existing = idem_store.get(idempotency_key)
        if existing is not None:
            existing_hash, existing_response = existing
            if existing_hash == body_hash:
                response.status_code = 200  # replay of a completed key -> the original response
                return existing_response
            raise HTTPException(
                status_code=409,
                detail={
                    "detail": "Idempotency-Key reused with a different request body",
                    "request_id": request_id,
                },
            )

    memory_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    memory_response: dict[str, Any] = {
        "id": memory_id,
        "content": body["content"],
        "content_type": body.get("content_type", "text"),
        "tier": body.get("tier", "stm"),
        "state": "active",
        "importance_score": body.get("importance_score", 0.5),
        "access_count": 0,
        "created_at": now,
        "updated_at": now,
        "namespace": _namespace_prefix(identity),
        "metadata": body.get("metadata", {}),
        "subject": body.get("subject"),
        "predicate": body.get("predicate"),
        "object": body.get("object"),
        "mention_count": 1,
        "relevance_score": 0.0,
        "parent_ids": [],
        "child_ids": [],
        "content_hash": body_hash,
    }
    tenant_store[memory_id] = memory_response
    if idempotency_key is not None:
        idem_store[idempotency_key] = (body_hash, memory_response)
    return memory_response


# ---- GET /v1/memories/{memory_id} (get) ---------------------------------------------------------
# NET-NEW route (B2, module docstring): a point-get by id, tenancy-scoped by identity exactly like
# every other route. 404 (never a fake row) when the id is unknown OR belongs to a different tenant
# — the SDK's `get()` maps that 404 to `None` (see `client.py`), never distinguishing "absent" from
# "cross-tenant" (same non-enumerating discipline `resolve_identity`'s 401 already follows).


@app.get("/v1/memories/{memory_id}")
async def get_memory(
    memory_id: str,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    request_id = _request_id(x_request_id)
    response.headers["X-Request-ID"] = request_id
    tenant_store = _memories.get(identity.tenant_key, {})
    memory = tenant_store.get(memory_id)
    if memory is None:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"memory {memory_id} not found", "request_id": request_id},
        )
    return memory


# ---- POST /v1/memories/{memory_id}/share (share) ----------------------------------------------
# NET-NEW route (B2, module docstring): the private->shared crossing verb. This fake has no
# separate private-plane store to cross FROM, so the simplest real implementation is: look the
# memory up in the caller's tenant store (404 if absent, same discipline as `get`), validate
# `visibility`, and return the row unchanged — `MemoryResponse` (Appendix A.1) carries no
# `visibility` field to mutate (see `client.py`'s `share()` docstring), so there is nothing else
# for a conforming server to do with this fake's flat in-memory shape.


class _ShareBody(BaseModel):
    visibility: str = Field(min_length=1)


@app.post("/v1/memories/{memory_id}/share")
async def share_memory(
    memory_id: str,
    body: _ShareBody,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    request_id = _request_id(x_request_id)
    response.headers["X-Request-ID"] = request_id
    if body.visibility not in ("shared", "private"):
        raise HTTPException(
            status_code=422,
            detail={
                "detail": f"visibility must be shared|private, got {body.visibility!r}",
                "request_id": request_id,
            },
        )
    tenant_store = _memories.get(identity.tenant_key, {})
    memory = tenant_store.get(memory_id)
    if memory is None:
        raise HTTPException(
            status_code=404,
            detail={"detail": f"memory {memory_id} not found", "request_id": request_id},
        )
    return memory


# ---- GET /memories (search) ------------------------------------------------------------------


@app.get("/memories")
async def search_memories(
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    query: str = "",
    limit: int = 10,
    tier: str | None = None,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    response.headers["X-Request-ID"] = _request_id(x_request_id)
    tenant_store = _memories.get(identity.tenant_key, {})
    items = list(tenant_store.values())
    if query:
        items = [m for m in items if query.lower() in m["content"].lower()]
    if tier:
        items = [m for m in items if m["tier"] == tier]
    items.sort(key=lambda m: m["created_at"], reverse=True)
    items = items[:limit]
    return {"memories": items, "total": len(items), "tier": tier}


# ---- POST /v1/memories/recall ------------------------------------------------------------------


class _RecallBody(BaseModel):
    text: str = Field(min_length=1)
    limit: int = 10
    channels: dict[str, bool] = Field(
        default_factory=lambda: {"stm": True, "mtm": True, "ltm": True}
    )
    mode: str = "ranked"
    persona: str | None = None
    max_tokens: int | None = None
    correlation_id: str | None = None


@app.post("/v1/memories/recall")
async def recall_memories(
    body: _RecallBody,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    tier: str | None = None,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """`?tier=` (net-new query param, mirrors the real demo server's `_effective_tier_filter`)
    always wins over `body.channels` when both are given: narrows both the matched-item filter
    AND the reported `channels_run` to exactly that one channel."""
    response.headers["X-Request-ID"] = _request_id(x_request_id)
    tenant_store = _memories.get(identity.tenant_key, {})
    matches = [m for m in tenant_store.values() if body.text.lower() in m["content"].lower()]
    channels_run = body.channels
    if tier is not None:
        if tier not in ("stm", "mtm", "ltm"):
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": f"tier must be one of stm|mtm|ltm, got {tier!r}",
                    "request_id": _request_id(x_request_id),
                },
            )
        matches = [m for m in matches if m["tier"] == tier]
        channels_run = {"stm": tier == "stm", "mtm": tier == "mtm", "ltm": tier == "ltm"}
    matches.sort(key=lambda m: m["created_at"], reverse=True)
    matches = matches[: body.limit]
    items = [
        {
            "memory_id": m["id"],
            "content": m["content"],
            "tier": m["tier"],
            "channel": "stm",
            "fused_score": 1.0 - (i * 0.01),
            "rerank_score": None,
            "is_floor": False,
            "artifact_ref": None,
        }
        for i, m in enumerate(matches)
    ]
    return {
        "namespace": {
            "org": "conformance-org",
            "workspace": identity.workspace_id,
            "user": "*",  # SHARED namespace requires user="*" (mu_contracts CANONICAL §1 rule 4)
            "session": identity.session_id,
            "visibility": "shared",
        },
        "items": items,
        "channels_run": channels_run,
        "degraded": None,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ---- POST /v1/memories/consolidate -------------------------------------------------------------
# NET-NEW route (task brief: "New wire routes (consolidate/ask) are net-new"). See the module-level
# `_facts`/`_consolidated_ids` docstring for the fake's supersession bookkeeping.


class _ConsolidateBody(BaseModel):
    limit: int = 50


@app.post("/v1/memories/consolidate")
async def consolidate_memories(
    body: _ConsolidateBody,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    response.headers["X-Request-ID"] = _request_id(x_request_id)
    tenant_store = _memories.get(identity.tenant_key, {})
    facts = _facts.setdefault(identity.tenant_key, {})
    swept_ids = _consolidated_ids.setdefault(identity.tenant_key, set())

    unswept = [
        m
        for m in sorted(tenant_store.values(), key=lambda m: m["created_at"])
        if m["id"] not in swept_ids and m.get("subject") and m.get("predicate") and m.get("object")
    ][: body.limit]

    added = 0
    superseded = 0
    for m in unswept:
        key = (m["subject"], m["predicate"])
        existing = facts.get(key)
        if existing is None or existing["object"] != m["object"]:
            if existing is not None and existing["object"] != m["object"]:
                superseded += 1  # invalidate-don't-delete: the prior (subject,predicate) loses
            facts[key] = {"object": m["object"], "memory_id": m["id"]}
            added += 1
        swept_ids.add(m["id"])

    return {
        "facts_extracted": len(unswept),
        "added": added,
        "superseded": superseded,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ---- POST /v1/memories/ask ---------------------------------------------------------------------
# NET-NEW route. Fake synthesis: answers from the CURRENT (post-supersession) active fact table
# only — never a raw recall item — so `ask()` vs `recall()` genuinely differ at the wire level.


class _AskBody(BaseModel):
    question: str = Field(min_length=1)
    limit: int = 10


@app.post("/v1/memories/ask")
async def ask_memory(
    body: _AskBody,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    response.headers["X-Request-ID"] = _request_id(x_request_id)
    facts = _facts.get(identity.tenant_key, {})
    question_lower = body.question.lower()
    hits = [
        f"{subject} {predicate} {fact['object']}"
        for (subject, predicate), fact in facts.items()
        if subject.lower() in question_lower
    ]
    answer = "; ".join(hits) if hits else "no consolidated facts match that question"
    return {
        "question": body.question,
        "answer": answer,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ---- POST /v1/context/window (build_context) -----------------------------------------------------
# NET-NEW route (B2, module docstring) — the PRIVATE-plane context-WINDOW helper
# (`MemoryClient.build_context()`'s wire twin, design §2.5 REVIEW-2 FIX 1). Deterministic recall +
# render, NO LLM synthesis: reuses the exact same tenant-scoped substring match `recall_memories`
# already does, projected into `RecallItemView`-shaped items (the canonical hit-item DTO
# `ContextView.items` shares with `RecallResult.items`, `SDK-BUILD-DECISIONS.md` Decision B
# cross-cutting section) — never the shared-plane `_DiscoverBody`/`ContextIndexListView` shape
# below, which is a completely different (governed-transfer) verb.


class _ContextWindowBody(BaseModel):
    text: str = Field(min_length=1)
    limit: int = 10
    max_chars: int | None = None


@app.post("/v1/context/window")
async def context_window(
    body: _ContextWindowBody,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    response.headers["X-Request-ID"] = _request_id(x_request_id)
    tenant_store = _memories.get(identity.tenant_key, {})
    matches = [m for m in tenant_store.values() if body.text.lower() in m["content"].lower()]
    matches.sort(key=lambda m: m["created_at"], reverse=True)
    matches = matches[: body.limit]
    items = [
        {
            "memory_id": m["id"],
            "content": m["content"],
            "tier": m["tier"],
            "channel": "stm",
            "fused_score": 1.0 - (i * 0.01),
            "rerank_score": None,
            "is_floor": False,
            "artifact_ref": None,
        }
        for i, m in enumerate(matches)
    ]
    return {"text": body.text, "items": items, "degraded": None}


# ---- POST /v1/context/discover -----------------------------------------------------------------


class _DiscoverBody(BaseModel):
    session_id: str = Field(min_length=1)


@app.post("/v1/context/discover")
async def discover_context(
    body: _DiscoverBody,
    identity: Annotated[Identity, Depends(resolve_identity)],
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    response.headers["X-Request-ID"] = _request_id(x_request_id)
    del identity  # tenancy already enforced by resolve_identity; nothing else needed for this stub
    return {
        "session_id": body.session_id,
        "indexes": [],
        "generated_at": datetime.now(UTC).isoformat(),
    }


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


# ---- GET /test/flaky/{key} --------------------------------------------------------------------
# Test-only route (not part of the frozen wire contract) proving the SDK's real retry decorator
# stack (`with_retry` around `MemoryClient._raw_request`) actually retries 429/503 responses —
# see `tests/integration/test_retry_conformance.py`. A per-key in-memory counter fails the first
# `fail_times` requests for that key with `status_code`, then succeeds; a companion endpoint lets
# the test observe the real attempt count server-side (zero mocks — the retry loop is real).

_flaky_attempts: dict[str, int] = {}  # key -> requests seen so far


@app.get("/test/flaky/{key}")
async def flaky_endpoint(
    key: str,
    fail_times: int = 2,
    status_code: int = 503,
    x_request_id: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    attempts = _flaky_attempts.get(key, 0) + 1
    _flaky_attempts[key] = attempts
    if attempts <= fail_times:
        raise HTTPException(
            status_code=status_code,
            detail={
                "detail": f"synthetic transient failure (attempt {attempts})",
                "request_id": _request_id(x_request_id),
            },
        )
    return {"attempts": attempts}


@app.get("/test/flaky/{key}/attempts")
async def flaky_attempts(key: str) -> dict[str, int]:
    return {"attempts": _flaky_attempts.get(key, 0)}
