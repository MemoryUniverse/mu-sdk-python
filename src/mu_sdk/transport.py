"""The `Transport` port — the seam that makes `httpx` swappable/testable (DEV-STANDARDS rule 5:
"repository pattern for all data access" generalized to the one external dependency this SDK has:
the HTTP client). `MemoryClient` never imports `httpx` directly outside `HttpxTransport`; every
other module talks to the `Transport` protocol.

Port-of-pattern: `mem0.MemoryClient.__init__` builds one `httpx.Client(base_url=..., headers=...,
timeout=...)` and reuses it for every call (`other_repos/mem0/mem0/client/main.py:87-94`). This
module keeps that "one client, reused" shape but behind a protocol so a conformance test can swap
in a deterministic fake transport for pure-unit tests (never for integration tests — those use the
real `HttpxTransport` over real TCP, DEV-STANDARDS "zero mocks").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from mu_sdk.errors import SdkTimeoutError, TransportError
from mu_sdk.settings import SdkSettings

__all__ = ["HttpxTransport", "Transport", "TransportResponse"]


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
