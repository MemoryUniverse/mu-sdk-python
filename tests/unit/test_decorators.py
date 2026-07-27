"""Pure-unit: the retry/timeout/trace decorator stack (`mu_sdk.decorators`). Uses a fake function
(NOT a mock of `httpx`/`Transport` internals — a plain async function standing in for the request
choke-point) to exercise retry counts, backoff-on-transient-only, timeout, trace-header injection,
and cancellation-safety in isolation."""

from __future__ import annotations

import asyncio

import pytest

from mu_sdk.decorators import with_retry, with_timeout, with_trace
from mu_sdk.errors import AuthenticationError, SdkTimeoutError, TransportError
from mu_sdk.transport import TransportResponse

pytestmark = pytest.mark.unit


async def test_retry_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}

    async def flaky(**kwargs: object) -> TransportResponse:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransportError("boom")
        return TransportResponse(status_code=200)

    wrapped = with_retry(max_retries=5, backoff_base_s=0.001, backoff_max_s=0.01)(flaky)
    result = await wrapped()
    assert result.status_code == 200
    assert calls["n"] == 3


async def test_retry_exhausts_and_reraises_the_transient_error() -> None:
    calls = {"n": 0}

    async def always_fails(**kwargs: object) -> TransportResponse:
        calls["n"] += 1
        raise TransportError("still boom")

    wrapped = with_retry(max_retries=2, backoff_base_s=0.001, backoff_max_s=0.01)(always_fails)
    with pytest.raises(TransportError):
        await wrapped()
    assert calls["n"] == 3  # 1 initial attempt + 2 retries


async def test_retry_never_retries_a_permanent_error() -> None:
    calls = {"n": 0}

    async def permanent_failure(**kwargs: object) -> TransportResponse:
        calls["n"] += 1
        raise AuthenticationError("bad creds")

    wrapped = with_retry(max_retries=5, backoff_base_s=0.001, backoff_max_s=0.01)(permanent_failure)
    with pytest.raises(AuthenticationError):
        await wrapped()
    assert calls["n"] == 1  # never retried


async def test_timeout_raises_sdk_timeout_error_and_does_not_leak_asyncio_timeout() -> None:
    async def slow(**kwargs: object) -> TransportResponse:
        await asyncio.sleep(1.0)
        return TransportResponse(status_code=200)

    wrapped = with_timeout(0.01)(slow)
    with pytest.raises(SdkTimeoutError):
        await wrapped()


async def test_timeout_does_not_swallow_outer_cancellation() -> None:
    """An outer cancellation of the whole call must propagate as CancelledError, never be
    reinterpreted as an SdkTimeoutError (DEV-STANDARDS rule 1)."""

    started = asyncio.Event()

    async def slow(**kwargs: object) -> TransportResponse:
        started.set()
        await asyncio.sleep(10.0)
        return TransportResponse(status_code=200)

    wrapped = with_timeout(30.0)(slow)  # generous budget: only the OUTER cancel should fire
    task = asyncio.create_task(wrapped())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_trace_mints_request_id_once_and_reuses_it_across_retries() -> None:
    seen_ids: list[str | None] = []

    async def flaky(**kwargs: object) -> TransportResponse:
        headers = kwargs.get("headers") or {}
        assert isinstance(headers, dict)
        seen_ids.append(headers.get("X-Request-ID"))
        if len(seen_ids) < 3:
            raise TransportError("boom")
        return TransportResponse(status_code=200)

    wrapped = with_trace()(
        with_retry(max_retries=5, backoff_base_s=0.001, backoff_max_s=0.01)(flaky)
    )
    await wrapped()
    assert len(seen_ids) == 3
    assert len(set(seen_ids)) == 1  # the SAME id across every retry attempt
    assert seen_ids[0] is not None


async def test_trace_respects_a_caller_supplied_request_id() -> None:
    seen: list[str | None] = []

    async def once(**kwargs: object) -> TransportResponse:
        headers = kwargs.get("headers") or {}
        assert isinstance(headers, dict)
        seen.append(headers.get("X-Request-ID"))
        return TransportResponse(status_code=200)

    wrapped = with_trace()(once)
    await wrapped(headers={"X-Request-ID": "caller-id"})
    assert seen == ["caller-id"]


async def test_rate_limited_retry_after_is_honored_and_capped() -> None:
    """A server-declared `Retry-After` of 100s must be CAPPED at `backoff_max_s` — proven by
    real elapsed wall-clock time staying well under what an uncapped 100s wait would cost,
    rather than mocking `asyncio.sleep` (which tenacity may or may not call by a patchable
    reference internally)."""
    from mu_sdk.errors import RateLimitedError

    calls = {"n": 0}

    async def rate_limited_then_ok(**kwargs: object) -> TransportResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitedError("slow down", retry_after_s=100.0)  # far above the cap
        return TransportResponse(status_code=200)

    wrapped = with_retry(max_retries=2, backoff_base_s=0.01, backoff_max_s=0.2)(
        rate_limited_then_ok
    )
    loop = asyncio.get_event_loop()
    started = loop.time()
    result = await wrapped()
    elapsed = loop.time() - started
    assert result.status_code == 200
    assert elapsed < 2.0  # would be ~100s if the server hint weren't capped at backoff_max_s
