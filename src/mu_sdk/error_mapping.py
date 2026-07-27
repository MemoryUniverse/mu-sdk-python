"""Maps a `TransportResponse` onto the typed SDK error hierarchy (`mu_sdk.errors`).

The frozen REST error envelope (api-mcp-surface-spec.md §2.4): unexpected failures are
``{"detail": str, "request_id": str}``; typed surface errors map to fixed status codes per the
table in that same section + `mu-local-and-sdk-spec.md` §7 (bad/revoked API key -> a
non-enumerating 404 mapped client-side to `AuthenticationError`). This module is the ONE place
that mapping happens — no route/verb re-implements its own status-code switch.
"""

from __future__ import annotations

from typing import Any

from mu_sdk.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PrivateDataRejectedError,
    RateLimitedError,
    SdkError,
    ServerError,
    ServiceUnavailableError,
    UnexpectedResponseError,
    ValidationError,
)
from mu_sdk.transport import TransportResponse

__all__ = ["raise_for_wire_error"]

_MCP_PRIVATE_DATA_REJECTED = "private_data_rejected"


def _extract_detail(body: Any) -> str | None:
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
        if isinstance(detail, str):
            return detail
    return None


def _extract_mcp_code(body: Any) -> str | None:
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str):
            return code
    return None


def _extract_request_id(body: Any, response: TransportResponse) -> str | None:
    """`request_id` is a BODY field on the frozen REST envelope (api-mcp-surface-spec.md §2.4:
    ``{"detail": str, "request_id": str}``) — prefer it, falling back to the `X-Request-ID`
    trace header (api-mcp-surface-spec.md §2.3) if the body is missing/malformed."""
    if isinstance(body, dict):
        request_id = body.get("request_id")
        if isinstance(request_id, str):
            return request_id
    return response.header("X-Request-ID")


def _parse_retry_after(response: TransportResponse) -> float | None:
    raw = response.header("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def raise_for_wire_error(response: TransportResponse) -> None:
    """Raises the matching typed `SdkError` for any non-2xx response; returns None on 2xx.
    Fail-loud on anything unrecognized (`UnexpectedResponseError`) rather than best-effort
    guessing (DEV-STANDARDS rule 8)."""
    status = response.status_code
    if 200 <= status < 300:
        return

    body = response.json_body
    detail = _extract_detail(body) or response.text or f"HTTP {status}"
    request_id = _extract_request_id(body, response)

    error: SdkError
    if status == 401:
        error = AuthenticationError(detail, request_id=request_id, status_code=status)
    elif status == 403:
        if _extract_mcp_code(body) == _MCP_PRIVATE_DATA_REJECTED:
            error = PrivateDataRejectedError(detail, request_id=request_id, status_code=status)
        else:
            error = AuthorizationError(detail, request_id=request_id, status_code=status)
    elif status == 404:
        # single non-enumerating 404: a cross-tenant probe and a bad/revoked API key both land
        # here (mu-local-and-sdk-spec.md §7) — but a 404 with no auth context is ambiguous by
        # DESIGN (the surface refuses to be the oracle), so the SDK maps it to `NotFoundError`
        # here; callers that need the "was this an auth problem?" distinction rely on `ping()`
        # (out of this phase's scope) rather than this generic path.
        error = NotFoundError(detail, request_id=request_id, status_code=status)
    elif status == 409:
        error = ConflictError(detail, request_id=request_id, status_code=status)
    elif status == 422:
        error = ValidationError(detail, request_id=request_id, status_code=status)
    elif status == 429:
        error = RateLimitedError(
            detail,
            request_id=request_id,
            status_code=status,
            retry_after_s=_parse_retry_after(response),
        )
    elif status == 503:
        error = ServiceUnavailableError(
            detail,
            request_id=request_id,
            status_code=status,
            retry_after_s=_parse_retry_after(response),
        )
    elif 500 <= status < 600:
        error = ServerError(detail, request_id=request_id, status_code=status)
    else:
        error = UnexpectedResponseError(detail, request_id=request_id, status_code=status)
    raise error
