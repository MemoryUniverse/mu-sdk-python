"""Cross-cutting decorators — retry/backoff, timeout, and trace-header injection
(DEV-STANDARDS rule 7: "anything repeated across many functions becomes a decorator, not
copy-paste"). Applied ONCE, at `MemoryClient.__init__`, around the single request choke-point
(`MemoryClient._raw_request`) — every public verb (`add`/`search`/`recall`/`context.discover`)
funnels through that one decorated call, so the cross-cutting behavior is never duplicated per
verb.

Composition order (outermost first): **trace -> retry -> timeout -> raw_request**.
- `trace` is outermost so a request id is minted ONCE per logical call and REUSED across every
  retry of that call (retries of the same logical operation should carry the same
  ``X-Request-ID`` for server-side correlation).
- `retry` wraps `timeout` so a per-attempt timeout is itself a retryable failure.
- `timeout` is innermost, bounding each individual attempt.

Retry delegates to `tenacity` (DEV-STANDARDS "delegate to the adopted libs... rather than
re-implement") and is cancellation-safe: `asyncio.CancelledError` is never in the retried
exception set (`TransientSdkError` and subclasses only), so an outer cancellation propagates
through `tenacity` untouched — `tenacity`'s `AsyncRetrying` awaits the wrapped coroutine directly
and does not catch `BaseException`.
"""

from __future__ import annotations

import asyncio
import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from mu_sdk.errors import SdkTimeoutError, TransientSdkError
from mu_sdk.transport import TransportResponse

__all__ = ["with_retry", "with_timeout", "with_trace"]

RequestFunc = Callable[..., Awaitable[TransportResponse]]


class _WaitWithRetryAfter:
    """Wait strategy: honor a server-declared ``Retry-After`` (surfaced on
    `RateLimitedError.retry_after_s` / `ServiceUnavailableError.retry_after_s`) when present,
    capped at `max_wait_s`; otherwise fall back to exponential backoff."""

    def __init__(self, base_s: float, max_wait_s: float) -> None:
        self._max_wait_s = max_wait_s
        self._exponential = wait_exponential(multiplier=base_s, max=max_wait_s)

    def __call__(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        if outcome is not None and outcome.failed:
            exc = outcome.exception()
            retry_after = getattr(exc, "retry_after_s", None)
            if isinstance(retry_after, int | float):
                return min(float(retry_after), self._max_wait_s)
        return float(self._exponential(retry_state))


def with_retry(
    *, max_retries: int, backoff_base_s: float, backoff_max_s: float
) -> Callable[[RequestFunc], RequestFunc]:
    """Retries only `TransientSdkError` subclasses (transport failure / local timeout /
    rate-limited / service-unavailable) — never an authentication/validation/not-found error,
    which are permanent per-call outcomes, not transient."""

    def decorator(func: RequestFunc) -> RequestFunc:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> TransportResponse:
            retrying = AsyncRetrying(
                stop=stop_after_attempt(max_retries + 1),
                wait=_WaitWithRetryAfter(backoff_base_s, backoff_max_s),
                retry=retry_if_exception_type(TransientSdkError),
                reraise=True,
            )
            async for attempt in retrying:
                with attempt:
                    return await func(*args, **kwargs)
            # unreachable: AsyncRetrying either returns via `attempt` or re-raises (reraise=True)
            raise AssertionError("unreachable: tenacity retry loop exited without a result")

        return wrapper

    return decorator


def with_timeout(timeout_s: float) -> Callable[[RequestFunc], RequestFunc]:
    """A hard wall-clock ceiling for the whole call (i.e. including any retries wrapped
    INSIDE this decorator — see the module docstring's composition order). Cancellation-safe:
    `asyncio.wait_for` cancels only the inner awaitable on a LOCAL timeout and raises
    `TimeoutError`; an outer cancellation of THIS coroutine still propagates as
    `asyncio.CancelledError`, never converted."""

    def decorator(func: RequestFunc) -> RequestFunc:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> TransportResponse:
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_s)
            except TimeoutError as exc:
                raise SdkTimeoutError(f"{func.__name__} exceeded the {timeout_s}s budget") from exc

        return wrapper

    return decorator


def with_trace() -> Callable[[RequestFunc], RequestFunc]:
    """Mints `X-Request-ID`/`X-Turn-ID` once per logical call if the caller did not already
    supply them (api-mcp-surface-spec.md §2.3: "minted if absent"), threading them through
    every retry of that same call via the closed-over `headers` kwarg."""

    def decorator(func: RequestFunc) -> RequestFunc:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> TransportResponse:
            headers: dict[str, str] = dict(kwargs.get("headers") or {})
            headers.setdefault("X-Request-ID", str(uuid.uuid4()))
            headers.setdefault("X-Turn-ID", str(uuid.uuid4()))
            kwargs["headers"] = headers
            return await func(*args, **kwargs)

        return wrapper

    return decorator
