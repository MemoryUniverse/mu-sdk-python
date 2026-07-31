"""The typed SDK error hierarchy — maps wire-observed failures (HTTP status + the frozen REST
error envelope ``{detail, request_id}``, api-mcp-surface-spec.md §2.4) onto typed exceptions.

Deliberately a SEPARATE hierarchy from ``mu_contracts.domain.errors.MemoryUniverseError``: that
hierarchy is the SERVER/ENGINE-side taxonomy adapters raise internally (its own docstring: "home:
mu-contracts... errors are part of the shared wire vocabulary so every plane/package maps the SAME
hierarchy" — i.e. shared *within the server-side planes*). The SDK is a CLIENT: it never
reconstructs a server exception from a wire response, it observes a status code + an envelope and
maps to its OWN client-side type (same pattern as `mem0.exceptions` / `stripe.error` / the OpenAI
Python SDK) — never trusts `detail` as anything but a human string (never re-raise the server's
internal exception name, never parse `detail` for control flow).

Fail-loud discipline (DEV-STANDARDS rule 8): every non-2xx response raises here. Nothing is
swallowed; there is no silent fallback path in this module.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "NotFoundError",
    "PrivateDataRejectedError",
    "RateLimitedError",
    "SdkError",
    "SdkTimeoutError",
    "ServerError",
    "ServiceUnavailableError",
    "SurfaceVerbNotImplementedError",
    "TransientSdkError",
    "TransportError",
    "UnexpectedResponseError",
    "ValidationError",
]


class SdkError(Exception):
    """Root of the mu-sdk client-side error hierarchy."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id
        self.status_code = status_code

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self.message!r}, "
            f"status_code={self.status_code!r}, request_id={self.request_id!r})"
        )


class TransientSdkError(SdkError):
    """Marker base for failures the retry decorator is allowed to retry (network-level,
    rate-limited, or a server-declared transient unavailability). Never raised directly —
    always one of the concrete subclasses below."""


class TransportError(TransientSdkError):
    """A network-level failure (connect/read error, DNS, TLS) — no HTTP response was received."""


class SdkTimeoutError(TransientSdkError):
    """The client-side timeout budget (`SdkSettings.timeout_s` or a per-call override) elapsed
    before a response was received. Distinct from a 503 `ServiceUnavailableError` — this one
    never reached the server, or the server never answered in time."""


class RateLimitedError(TransientSdkError):
    """HTTP 429 — the gateway rate class was exceeded (api-mcp-surface-spec.md §2.7)."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, request_id=request_id, status_code=status_code)
        self.retry_after_s = retry_after_s


class ServiceUnavailableError(TransientSdkError):
    """HTTP 503 — a named degrade (e.g. `SyncStatusUnavailableError`, api-mcp-surface-spec.md
    §8) surfaced as a fail-visible unavailability. The SDK never downgrades this to a synthesized
    success (§4.8's "fail-visible rule" applies at the SDK boundary too)."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, request_id=request_id, status_code=status_code)
        self.retry_after_s = retry_after_s


class AuthenticationError(SdkError):
    """HTTP 401 — missing/invalid/revoked credential (mu-local-and-sdk-spec.md §7 T5:
    ``MemoryClient(api_key="bad")`` raises this at ``ping()``; a revoked key mid-session maps
    from a non-enumerating 404 to this same type per that spec's error table)."""


class AuthorizationError(SdkError):
    """HTTP 403 — the caller is authenticated but the operation is denied."""


class PrivateDataRejectedError(AuthorizationError):
    """A PRIVATE-visibility write was rejected by the SHARED-only route (`app.py:1690`;
    api-mcp-surface-spec.md §4.3 — "shared POST /memories rejects visibility=PRIVATE"). The SDK
    has no private->shared leak path (parent §5.6) — this is the client-visible proof of that."""


class NotFoundError(SdkError):
    """HTTP 404 — including the deliberate single non-enumerating 404 a cross-tenant probe gets
    (api-mcp-surface-spec.md §2.2/§2.4): the SDK cannot and must not distinguish "denied" from
    "absent" from this exception alone."""


class ConflictError(SdkError):
    """HTTP 409 — a conflicting idempotent replay (`Idempotency-Key` reused with a different
    body, api-mcp-surface-spec.md §2.3) or a resolve-on-terminal-record conflict."""


class ValidationError(SdkError):
    """HTTP 422 — the request failed server-side validation (distinct from a client-side
    pydantic `ValidationError`, which is raised before any request is even sent)."""


class ServerError(SdkError):
    """HTTP 5xx other than 503 — an unexpected server failure. REST envelope is
    ``{"detail": str, "request_id": str}`` (api-mcp-surface-spec.md §2.4); never leaks a
    stack trace or internal exception name, by the server's own contract."""


class UnexpectedResponseError(SdkError):
    """The response did not match ANY known shape (bad status code range, non-JSON body where
    JSON was expected, a missing required envelope field). Fail-loud rather than best-effort
    guessing — DEV-STANDARDS rule 8."""


class SurfaceVerbNotImplementedError(SdkError):
    """`promote`/`demote` have no engine/wire counterpart anywhere in the tree yet (build-queue
    item 5; design §2.5's unified-verb-surface proposal): "the facade method raises
    `NotImplementedError` (never a fake 200) until the engine counterpart lands"
    (api-mcp-surface-spec.md §4.3b). `MemoryClient.promote()`/`.demote()` raise this NAMED error
    (`status_code=501`) immediately, with NO network call — there is nothing to call. Mirrors
    `mu_engine.surface.facade.SurfaceVerbNotImplementedError` (the embedded-side twin
    `LocalMemory`'s `SurfaceFacade` raises for the identical reason) — a deliberate SAME-named
    local twin, not an import, since `mu_sdk` may never import `mu_engine`
    (PACKAGING-v2.md §5 "sdk-has-no-engine" boundary).

    Also mapped here by `error_mapping.raise_for_wire_error` from a genuine wire HTTP 501, for the
    day a real server actually serves one (Stage C) — never a silent no-op or partial success on
    either path (DEV-STANDARDS rule 8)."""
